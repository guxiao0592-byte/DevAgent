package com.devagent.eclipse.preferences;

import org.eclipse.jface.preference.FieldEditorPreferencePage;
import org.eclipse.jface.preference.StringFieldEditor;
import org.eclipse.ui.IWorkbench;
import org.eclipse.ui.IWorkbenchPreferencePage;

/**
 * Eclipse preference page for DevAgent configuration.
 */
public class DevAgentPreferencePage extends FieldEditorPreferencePage
        implements IWorkbenchPreferencePage {

    public DevAgentPreferencePage() {
        super(GRID);
        setTitle("DevAgent");
        setDescription("Configure DevAgent integration settings");
    }

    @Override
    public void init(IWorkbench workbench) {
        // Initialize preference store
        setPreferenceStore(
            new org.eclipse.jface.preference.PreferenceStore(
                System.getProperty("user.home") + "/.devagent/eclipse_prefs.properties"
            )
        );
    }

    @Override
    protected void createFieldEditors() {
        addField(new StringFieldEditor(
            "apiUrl",
            "API Server URL:",
            getFieldEditorParent()
        ) {
            {
                setStringValue("http://127.0.0.1:8911");
            }
        });

        addField(new StringFieldEditor(
            "outputDir",
            "Default Output Directory:",
            getFieldEditorParent()
        ) {
            {
                setStringValue("${workspace_loc}/outputs");
            }
        });

        addField(new StringFieldEditor(
            "maxRetry",
            "Max Retry Count:",
            getFieldEditorParent()
        ) {
            {
                setStringValue("2");
            }
        });
    }
}
