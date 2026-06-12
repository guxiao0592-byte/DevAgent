package com.devagent.intellij.settings;

import com.intellij.openapi.components.PersistentStateComponent;
import com.intellij.openapi.components.State;
import com.intellij.openapi.components.Storage;
import com.intellij.openapi.project.Project;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

/**
 * Persistent settings for DevAgent plugin.
 * Stored in the project's workspace file.
 */
@State(
    name = "DevAgentSettings",
    storages = @Storage("devagent-settings.xml")
)
public class DevAgentSettings implements PersistentStateComponent<DevAgentSettings.State> {

    public String apiUrl = "http://127.0.0.1:8911";
    public String outputDir = "outputs";
    public int maxRetry = 2;
    public boolean autoAnalyzeOnSave = false;

    public static class State {
        public String apiUrl;
        public String outputDir;
        public int maxRetry;
        public boolean autoAnalyzeOnSave;
    }

    @Nullable
    @Override
    public State getState() {
        State state = new State();
        state.apiUrl = apiUrl;
        state.outputDir = outputDir;
        state.maxRetry = maxRetry;
        state.autoAnalyzeOnSave = autoAnalyzeOnSave;
        return state;
    }

    @Override
    public void loadState(@NotNull State state) {
        this.apiUrl = state.apiUrl != null ? state.apiUrl : "http://127.0.0.1:8911";
        this.outputDir = state.outputDir != null ? state.outputDir : "outputs";
        this.maxRetry = state.maxRetry > 0 ? state.maxRetry : 2;
        this.autoAnalyzeOnSave = state.autoAnalyzeOnSave;
    }

    @Nullable
    public static DevAgentSettings getInstance(Project project) {
        if (project == null) return null;
        return project.getService(DevAgentSettings.class);
    }
}
