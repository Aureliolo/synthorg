import pluginVue from 'eslint-plugin-vue'
import pluginSecurity from 'eslint-plugin-security'
import tsParser from '@typescript-eslint/parser'

export default [
  {
    ignores: ['dist/**'],
  },
  ...pluginVue.configs['flat/essential'],
  pluginSecurity.configs.recommended,
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tsParser,
      },
    },
  },
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: tsParser,
    },
  },
  {
    rules: {
      'vue/no-v-html': 'warn',
    },
  },
  {
    files: ['src/App.vue', 'src/components/layout/Sidebar.vue', 'src/components/layout/Topbar.vue'],
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
]
