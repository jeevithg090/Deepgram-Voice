'use strict';

/**
 * Light / dark theme handling shared by every page.
 *
 * Loaded synchronously in <head> so the saved theme is applied before first paint
 * (no flash of the wrong theme). Without a saved choice the OS preference is used.
 */
(function () {
    const STORAGE_KEY = 'voice-agent-theme';
    const root = document.documentElement;

    function readSaved() {
        try {
            const value = window.localStorage.getItem(STORAGE_KEY);
            return value === 'light' || value === 'dark' ? value : null;
        } catch (_) {
            return null; // storage blocked (private mode, disabled cookies)
        }
    }

    function save(theme) {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (_) {
            /* ignore: the choice just won't persist */
        }
    }

    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    const systemTheme = () => (media && !media.matches ? 'light' : 'dark');
    const currentTheme = () => root.dataset.theme || systemTheme();

    const saved = readSaved();
    if (saved) root.dataset.theme = saved;

    function syncToggle(button) {
        const theme = currentTheme();
        const next = theme === 'dark' ? 'light' : 'dark';
        button.setAttribute('aria-label', `Switch to ${next} theme`);
        button.title = `Switch to ${next} theme`;
        button.dataset.current = theme;
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', theme === 'dark' ? '#0a0a0a' : '#ffffff');
    }

    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('theme-toggle');
        if (!button) return;
        syncToggle(button);
        button.addEventListener('click', () => {
            const next = currentTheme() === 'dark' ? 'light' : 'dark';
            root.dataset.theme = next;
            save(next);
            syncToggle(button);
        });
        if (media && media.addEventListener) {
            media.addEventListener('change', () => syncToggle(button));
        }
    });
})();
