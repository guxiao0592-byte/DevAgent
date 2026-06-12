package com.devagent.eclipse.views;

import org.eclipse.swt.SWT;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.*;
import org.eclipse.ui.part.ViewPart;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

/**
 * Eclipse view for DevAgent task monitoring.
 * Displays recent task history and output.
 */
public class DevAgentView extends ViewPart {

    public static final String ID = "com.devagent.eclipse.views.DevAgentView";

    private Text outputText;
    private List taskList;

    @Override
    public void createPartControl(Composite parent) {
        Composite container = new Composite(parent, SWT.NONE);
        container.setLayout(new GridLayout(1, false));

        // Task list section
        Label taskLabel = new Label(container, SWT.NONE);
        taskLabel.setText("Recent Tasks:");

        taskList = new List(container, SWT.BORDER | SWT.V_SCROLL);
        GridData listData = new GridData(SWT.FILL, SWT.FILL, true, false);
        listData.heightHint = 100;
        taskList.setLayoutData(listData);
        taskList.add("No tasks executed yet");

        // Output section
        Label outputLabel = new Label(container, SWT.NONE);
        outputLabel.setText("Output:");

        outputText = new Text(container, SWT.MULTI | SWT.BORDER | SWT.V_SCROLL | SWT.READ_ONLY);
        GridData textData = new GridData(SWT.FILL, SWT.FILL, true, true);
        outputText.setLayoutData(textData);
        outputText.setText("DevAgent view ready.\n");

        // Button toolbar
        Composite buttonBar = new Composite(container, SWT.NONE);
        buttonBar.setLayout(new GridLayout(3, true));
        buttonBar.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));

        Button healthBtn = new Button(buttonBar, SWT.PUSH);
        healthBtn.setText("Check API Health");
        healthBtn.addListener(SWT.Selection, e -> checkApiHealth());

        Button clearBtn = new Button(buttonBar, SWT.PUSH);
        clearBtn.setText("Clear Output");
        clearBtn.addListener(SWT.Selection, e -> outputText.setText(""));

        Button refreshBtn = new Button(buttonBar, SWT.PUSH);
        refreshBtn.setText("Refresh");
        refreshBtn.addListener(SWT.Selection, e -> refreshTaskList());
    }

    private void checkApiHealth() {
        outputText.append("\n[DevAgent] Checking API health...\n");
        try {
            HttpClient client = HttpClient.newHttpClient();
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create("http://127.0.0.1:8911/health"))
                .GET()
                .build();

            client.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenAccept(response -> {
                    Display.getDefault().asyncExec(() -> {
                        if (response.statusCode() == 200) {
                            outputText.append("[DevAgent] API server is healthy\n");
                        } else {
                            outputText.append("[DevAgent] API returned: " + response.statusCode() + "\n");
                        }
                    });
                })
                .exceptionally(ex -> {
                    Display.getDefault().asyncExec(() -> {
                        outputText.append("[DevAgent] Connection failed: " + ex.getMessage() + "\n");
                    });
                    return null;
                });
        } catch (Exception ex) {
            outputText.append("[DevAgent] Error: " + ex.getMessage() + "\n");
        }
    }

    private void refreshTaskList() {
        taskList.removeAll();
        taskList.add("DevAgent tasks (refresh from API)");
        outputText.append("[DevAgent] Task list refreshed\n");
    }

    @Override
    public void setFocus() {
        outputText.setFocus();
    }
}
