import pluginVue from 'eslint-plugin-vue'
import tsParser from '@typescript-eslint/parser'

export default [
  ...pluginVue.configs['flat/essential'],
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
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'warn',
    },
  },
  {
    ignores: ['dist/'],
  },
]
