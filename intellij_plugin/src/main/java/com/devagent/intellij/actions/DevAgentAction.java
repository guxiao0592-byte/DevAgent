package com.devagent.intellij.actions;

import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.actionSystem.CommonDataKeys;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.ProgressManager;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.VirtualFile;
import org.jetbrains.annotations.NotNull;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;

/**
 * IntelliJ action that triggers a DevAgent task via the HTTP API.
 * Supports design, implement, repair, and full pipeline tasks.
 */
public class DevAgentAction extends AnAction {

    @Override
    public void actionPerformed(@NotNull AnActionEvent e) {
        Project project = e.getProject();
        if (project == null) return;

        String actionId = e.getActionManager().getId(this);
        String taskType = mapActionIdToTaskType(actionId);
        String displayName = mapActionIdToDisplayName(actionId);

        // Get current file as input
        VirtualFile currentFile = e.getData(CommonDataKeys.VIRTUAL_FILE);
        String inputPath = currentFile != null ? currentFile.getPath() : "";

        String apiUrl = getApiUrl(project);
        String outputDir = project.getBasePath() + "/outputs";

        // Execute in background with progress
        ProgressManager.getInstance().run(new Task.Backgroundable(project, "DevAgent: " + displayName, true) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(false);
                indicator.setText("Running DevAgent " + displayName + "...");

                try {
                    String jsonPayload = String.format(
                        "{\"task\":\"%s\",\"input\":\"%s\",\"output\":\"%s\",\"max_retry\":2}",
                        taskType, inputPath, outputDir
                    );

                    HttpClient client = HttpClient.newHttpClient();
                    HttpRequest request = HttpRequest.newBuilder()
                        .uri(URI.create(apiUrl + "/api/v1/tasks"))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(jsonPayload, StandardCharsets.UTF_8))
                        .build();

                    indicator.setText("Connecting to DevAgent API...");
                    HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());

                    if (response.statusCode() == 200) {
                        indicator.setText("Task completed successfully");
                        com.intellij.openapi.ui.Messages.showInfoMessage(
                            project,
                            "DevAgent " + displayName + " completed!\n" + response.body(),
                            "DevAgent"
                        );
                    } else {
                        throw new RuntimeException("HTTP " + response.statusCode() + ": " + response.body());
                    }
                } catch (Exception ex) {
                    com.intellij.openapi.ui.Messages.showErrorDialog(
                        project,
                        "DevAgent task failed: " + ex.getMessage(),
                        "DevAgent Error"
                    );
                }
            }
        });
    }

    @Override
    public void update(@NotNull AnActionEvent e) {
        // Enable action when project is open
        Project project = e.getProject();
        e.getPresentation().setEnabledAndVisible(project != null);
    }

    private String mapActionIdToTaskType(String actionId) {
        if (actionId == null) return "full";
        switch (actionId) {
            case "DevAgent.AnalyzeRequirement": return "design";
            case "DevAgent.GenerateCode": return "implement";
            case "DevAgent.RepairBug": return "repair";
            case "DevAgent.FullPipeline": return "full";
            default: return "full";
        }
    }

    private String mapActionIdToDisplayName(String actionId) {
        if (actionId == null) return "Task";
        switch (actionId) {
            case "DevAgent.AnalyzeRequirement": return "Analyze Requirement";
            case "DevAgent.GenerateCode": return "Generate Implementation";
            case "DevAgent.RepairBug": return "Repair Bug";
            case "DevAgent.FullPipeline": return "Full Pipeline";
            default: return "Task";
        }
    }

    private String getApiUrl(Project project) {
        // Read from persistent settings
        com.devagent.intellij.settings.DevAgentSettings settings =
            com.devagent.intellij.settings.DevAgentSettings.getInstance(project);
        if (settings != null && settings.apiUrl != null && !settings.apiUrl.isEmpty()) {
            return settings.apiUrl;
        }
        // Default URL
        return "http://127.0.0.1:8911";
    }
}
