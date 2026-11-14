import js from '@eslint/js';
import globals from 'globals';

export default [
    js.configs.recommended,
    {
        files: ['frontend/**/*.js'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'script',
            globals: globals.browser,
        },
        rules: {
            'no-unused-vars': ['error', { caughtErrors: 'none' }],
            eqeqeq: 'error',
            'prefer-const': 'error',
        },
    },
];
