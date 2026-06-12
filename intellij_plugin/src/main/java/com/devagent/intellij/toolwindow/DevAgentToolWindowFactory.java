package com.devagent.intellij.toolwindow;

import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;
import org.jetbrains.annotations.NotNull;

import javax.swing.*;
import java.awt.*;

/**
 * Factory for the DevAgent tool window.
 * Displays task history and status.
 */
public class DevAgentToolWindowFactory implements ToolWindowFactory {

    @Override
    public void createToolWindowContent(@NotNull Project project, @NotNull ToolWindow toolWindow) {
        DevAgentToolWindowPanel panel = new DevAgentToolWindowPanel(project);
        ContentFactory contentFactory = ContentFactory.SERVICE.getInstance();
        Content content = contentFactory.createContent(panel, "", false);
        toolWindow.getContentManager().addContent(content);
    }

    /**
     * Panel displayed inside the DevAgent tool window.
     */
    private static class DevAgentToolWindowPanel extends JPanel {
        private final Project project;
        private final DefaultListModel<String> taskListModel;
        private final JList<String> taskList;
        private final JTextArea outputArea;

        public DevAgentToolWindowPanel(Project project) {
            this.project = project;
            setLayout(new BorderLayout(5, 5));
            setBorder(BorderFactory.createEmptyBorder(5, 5, 5, 5));

            // Top: Task list
            taskListModel = new DefaultListModel<>();
            taskListModel.addElement("No tasks executed yet");
            taskList = new JList<>(taskListModel);
            taskList.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);

            JPanel topPanel = new JPanel(new BorderLayout());
            topPanel.setBorder(BorderFactory.createTitledBorder("Recent Tasks"));
            topPanel.add(new JScrollPane(taskList), BorderLayout.CENTER);
            topPanel.setPreferredSize(new Dimension(400, 150));

            // Bottom: Output area
            outputArea = new JTextArea();
            outputArea.setEditable(false);
            outputArea.setFont(new Font("Monospaced", Font.PLAIN, 12));

            JPanel bottomPanel = new JPanel(new BorderLayout());
            bottomPanel.setBorder(BorderFactory.createTitledBorder("Output"));
            bottomPanel.add(new JScrollPane(outputArea), BorderLayout.CENTER);

            // Split pane
            JSplitPane splitPane = new JSplitPane(JSplitPane.VERTICAL_SPLIT, topPanel, bottomPanel);
            splitPane.setResizeWeight(0.3);
            add(splitPane, BorderLayout.CENTER);

            // Button panel
            JPanel buttonPanel = new JPanel(new FlowLayout(FlowLayout.LEFT));
            JButton refreshBtn = new JButton("Refresh");
            refreshBtn.addActionListener(e -> refreshOutput());
            buttonPanel.add(refreshBtn);

            JButton connectBtn = new JButton("Check API Health");
            connectBtn.addActionListener(e -> checkApiHealth());
            buttonPanel.add(connectBtn);

            add(buttonPanel, BorderLayout.SOUTH);
        }

        private void refreshOutput() {
            int index = taskList.getSelectedIndex();
            if (index >= 0) {
                outputArea.append("[DevAgent] Selected task: " + taskListModel.get(index) + "\n");
            }
        }

        private void checkApiHealth() {
            outputArea.append("[DevAgent] Checking API health...\n");
            try {
                java.net.http.HttpClient client = java.net.http.HttpClient.newHttpClient();
                java.net.http.HttpRequest request = java.net.http.HttpRequest.newBuilder()
                    .uri(java.net.URI.create("http://127.0.0.1:8911/health"))
                    .GET()
                    .build();
                client.sendAsync(request, java.net.http.HttpResponse.BodyHandlers.ofString())
                    .thenAccept(response -> {
                        SwingUtilities.invokeLater(() -> {
                            if (response.statusCode() == 200) {
                                outputArea.append("[DevAgent] API server is healthy\n");
                            } else {
                                outputArea.append("[DevAgent] API server returned: " + response.statusCode() + "\n");
                            }
                        });
                    })
                    .exceptionally(ex -> {
                        SwingUtilities.invokeLater(() -> {
                            outputArea.append("[DevAgent] Cannot connect to API: " + ex.getMessage() + "\n");
                        });
                        return null;
                    });
            } catch (Exception ex) {
                outputArea.append("[DevAgent] Error: " + ex.getMessage() + "\n");
            }
        }
    }
}
