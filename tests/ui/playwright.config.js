// UI smoke tests (spec 009, US5). Offline by construction: fixtures.js routes
// every request to the working tree or node_modules, or aborts it.
'use strict';
const fs = require('fs');
const { defineConfig } = require('@playwright/test');

const PORT = Number(process.env.UI_PORT || 8765);
// The sandbox ships a Chromium at /opt/pw-browsers and must never run
// `playwright install`; CI installs the matching build and leaves this unset.
const LOCAL_CHROMIUM = process.env.PW_CHROMIUM ||
  (fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined);

module.exports = defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.js$/,
  timeout: 60000,
  retries: 0,
  workers: process.env.CI ? 2 : 3,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    browserName: 'chromium',
    launchOptions: LOCAL_CHROMIUM
      ? { executablePath: LOCAL_CHROMIUM, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] }
      : { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] }
  },
  webServer: {
    command: `node serve.js`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: !process.env.CI,
    env: { UI_PORT: String(PORT) }
  }
});
