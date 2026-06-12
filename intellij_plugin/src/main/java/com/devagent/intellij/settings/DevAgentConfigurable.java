package com.devagent.intellij.settings;

import com.intellij.openapi.options.Configurable;
import com.intellij.openapi.options.ConfigurationException;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.Nls;
import org.jetbrains.annotations.Nullable;

import javax.swing.*;
import java.awt.*;

/**
 * Settings UI for DevAgent plugin configuration.
 */
public class DevAgentConfigurable implements Configurable {

    private final Project project;
    private JTextField apiUrlField;
    private JTextField outputDirField;
    private JTextField maxRetryField;
    private JCheckBox autoAnalyzeCheckbox;
    private JPanel mainPanel;

    public DevAgentConfigurable(Project project) {
        this.project = project;
    }

    @Nls(capitalization = Nls.Capitalization.Title)
    @Override
    public String getDisplayName() {
        return "DevAgent";
    }

    @Nullable
    @Override
    public JComponent createComponent() {
        mainPanel = new JPanel(new GridBagLayout());
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(5, 5, 5, 5);
        c.fill = GridBagConstraints.HORIZONTAL;

        // API URL
        c.gridx = 0; c.gridy = 0;
        mainPanel.add(new JLabel("API Server URL:"), c);
        c.gridx = 1; c.weightx = 1.0;
        apiUrlField = new JTextField("http://127.0.0.1:8911", 30);
        mainPanel.add(apiUrlField, c);

        // Output Directory
        c.gridx = 0; c.gridy = 1; c.weightx = 0;
        mainPanel.add(new JLabel("Output Directory:"), c);
        c.gridx = 1; c.weightx = 1.0;
        outputDirField = new JTextField("outputs", 30);
        mainPanel.add(outputDirField, c);

        // Max Retry
        c.gridx = 0; c.gridy = 2; c.weightx = 0;
        mainPanel.add(new JLabel("Max Retry Count:"), c);
        c.gridx = 1; c.weightx = 1.0;
        maxRetryField = new JTextField("2", 10);
        mainPanel.add(maxRetryField, c);

        // Auto Analyze on Save
        c.gridx = 0; c.gridy = 3; c.weightx = 0;
        autoAnalyzeCheckbox = new JCheckBox("Auto-analyze file on save");
        c.gridx = 1;
        mainPanel.add(autoAnalyzeCheckbox, c);

        // Fill remaining space
        c.gridx = 0; c.gridy = 4; c.weighty = 1.0;
        mainPanel.add(new JPanel(), c);

        return mainPanel;
    }

    @Override
    public boolean isModified() {
        DevAgentSettings settings = DevAgentSettings.getInstance(project);
        if (settings == null) return false;
        return !apiUrlField.getText().equals(settings.apiUrl)
            || !outputDirField.getText().equals(settings.outputDir)
            || !maxRetryField.getText().equals(String.valueOf(settings.maxRetry))
            || autoAnalyzeCheckbox.isSelected() != settings.autoAnalyzeOnSave;
    }

    @Override
    public void apply() throws ConfigurationException {
        DevAgentSettings settings = DevAgentSettings.getInstance(project);
        if (settings == null) return;

        try {
            int retry = Integer.parseInt(maxRetryField.getText().trim());
            if (retry < 1) throw new NumberFormatException();
            settings.maxRetry = retry;
        } catch (NumberFormatException e) {
            throw new ConfigurationException("Max retry must be a positive integer");
        }

        settings.apiUrl = apiUrlField.getText().trim();
        settings.outputDir = outputDirField.getText().trim();
        settings.autoAnalyzeOnSave = autoAnalyzeCheckbox.isSelected();
    }

    @Override
    public void reset() {
        DevAgentSettings settings = DevAgentSettings.getInstance(project);
        if (settings == null) return;
        apiUrlField.setText(settings.apiUrl);
        outputDirField.setText(settings.outputDir);
        maxRetryField.setText(String.valueOf(settings.maxRetry));
        autoAnalyzeCheckbox.setSelected(settings.autoAnalyzeOnSave);
    }
}
