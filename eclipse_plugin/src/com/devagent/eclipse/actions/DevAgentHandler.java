package com.devagent.eclipse.actions;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.commands.ExecutionException;
import org.eclipse.core.runtime.IProgressMonitor;
import org.eclipse.core.runtime.IStatus;
import org.eclipse.core.runtime.Status;
import org.eclipse.core.runtime.jobs.Job;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.handlers.HandlerUtil;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IFileEditorInput;
import org.eclipse.core.resources.IFile;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;

/**
 * Eclipse handler for DevAgent commands.
 * Executes tasks via the DevAgent HTTP API.
 */
public class DevAgentHandler extends AbstractHandler {

    private static final String DEFAULT_API_URL = "http://127.0.0.1:8911";

    @Override
    public Object execute(ExecutionEvent event) throws ExecutionException {
        IWorkbenchWindow window = HandlerUtil.getActiveWorkbenchWindowChecked(event);
        String commandId = event.getCommand().getId();
        String taskType = mapCommandToTaskType(commandId);
        String displayName = mapCommandToDisplayName(commandId);

        // Get the current editor's file path as input
        String inputPath = "";
        IEditorPart editor = window.getActivePage().getActiveEditor();
        if (editor != null && editor.getEditorInput() instanceof IFileEditorInput) {
            IFile file = ((IFileEditorInput) editor.getEditorInput()).getFile();
            inputPath = file.getLocation().toOSString();
        }

        final String finalInputPath = inputPath;
        final String apiUrl = getApiUrl();

        // Run as background job
        Job job = new Job("DevAgent: " + displayName) {
            @Override
            protected IStatus run(IProgressMonitor monitor) {
                monitor.beginTask("Running DevAgent " + displayName, 100);

                try {
                    monitor.subTask("Connecting to DevAgent API...");

                    String jsonPayload = String.format(
                        "{\"task\":\"%s\",\"input\":\"%s\",\"output\":\"./outputs\",\"max_retry\":2}",
                        taskType, finalInputPath
                    );

                    HttpClient client = HttpClient.newHttpClient();
                    HttpRequest request = HttpRequest.newBuilder()
                        .uri(URI.create(apiUrl + "/api/v1/tasks"))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(jsonPayload, StandardCharsets.UTF_8))
                        .build();

                    monitor.worked(50);
                    monitor.subTask("Executing...");

                    HttpResponse<String> response = client.send(request,
                        HttpResponse.BodyHandlers.ofString());

                    monitor.worked(50);

                    Display.getDefault().asyncExec(() -> {
                        if (response.statusCode() == 200) {
                            MessageDialog.openInformation(
                                window.getShell(),
                                "DevAgent",
                                displayName + " completed successfully!\n\n" + response.body());
                        } else {
                            MessageDialog.openError(
                                window.getShell(),
                                "DevAgent Error",
                                "Task failed: HTTP " + response.statusCode() + "\n" + response.body());
                        }
                    });

                    monitor.done();
                    return Status.OK_STATUS;
                } catch (Exception e) {
                    Display.getDefault().asyncExec(() -> {
                        MessageDialog.openError(
                            window.getShell(),
                            "DevAgent Error",
                            "Failed to execute task: " + e.getMessage());
                    });
                    return Status.error("DevAgent task failed", e);
                }
            }
        };
        job.setUser(true);
        job.schedule();

        return null;
    }

    private String mapCommandToTaskType(String commandId) {
        if (commandId == null) return "full";
        switch (commandId) {
            case "com.devagent.eclipse.command.analyzeRequirement": return "design";
            case "com.devagent.eclipse.command.generateCode": return "implement";
            case "com.devagent.eclipse.command.repairBug": return "repair";
            case "com.devagent.eclipse.command.fullPipeline": return "full";
            default: return "full";
        }
    }

    private String mapCommandToDisplayName(String commandId) {
        if (commandId == null) return "Task";
        switch (commandId) {
            case "com.devagent.eclipse.command.analyzeRequirement": return "Analyze Requirement";
            case "com.devagent.eclipse.command.generateCode": return "Generate Code";
            case "com.devagent.eclipse.command.repairBug": return "Repair Bug";
            case "com.devagent.eclipse.command.fullPipeline": return "Full Pipeline";
            default: return "Task";
        }
    }

    private String getApiUrl() {
        // Read from Eclipse preferences
        String url = org.eclipse.jface.preference.IPreferenceStore.getString("apiUrl");
        return (url != null && !url.isEmpty()) ? url : DEFAULT_API_URL;
    }
}
