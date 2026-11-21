import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (/node_modules\/(react-dom|react|scheduler)\//.test(id)) return 'react-vendor';
            if (/node_modules\/(react-markdown|remark-|rehype-|micromark|mdast|hast|unist|vfile|property-information|space-separated-tokens|comma-separated-tokens|decode-named-character-reference|character-entities|devlop|estree|trim-lines|trough|unified|bail|is-plain-obj|extend|ccount|escape-string-regexp|markdown-table|longest-streak|zwitch|html-void-elements|web-namespaces)/.test(id)) {
              return 'markdown-vendor';
            }
            return 'vendor';
          }
          if (/\/src\/components\/(AgentSettings|ModelConnections|FeishuConnection|FleetNodes|SkillsSettings|ReadinessPanel|PlatformWorkspace|PlatformLibraries|PlatformOperations|PlatformJobInspector|ProjectWorkspace|TodoBoard|TodoGroupStandards)\./.test(id)) {
            return 'workspaces';
          }
          if (/\/src\/components\/(GroupWorkspace|GroupRoutines|RoutineDesigner|GroupAgentTasks|MuseDesktopPanel|MissionWorkspace|MissionTeamPicker|RoleContractFields)\./.test(id)) {
            return 'collaboration';
          }
          return undefined;
        },
      },
    },
  },
})
