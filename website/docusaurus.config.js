// @ts-check
// Docs for Lecture Lens. Docs-only site: the docs are served from the site root.
const { themes } = require('prism-react-renderer');

const owner = process.env.GITHUB_REPOSITORY_OWNER || 'opnsrcntrbtr';
const repo = (process.env.GITHUB_REPOSITORY || 'opnsrcntrbtr/lecture-lens').split('/')[1];

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Lecture Lens',
  tagline: 'Your lectures, turned into study material on your own Mac',
  favicon: 'img/logo.svg',
  url: process.env.DOCS_URL || `https://${owner}.github.io`,
  baseUrl: process.env.DOCS_BASE_URL || '/lecture-lens/',
  organizationName: owner,
  projectName: repo,
  trailingSlash: false,
  onBrokenLinks: 'throw',
  markdown: {
    format: 'detect', // .md is CommonMark, .mdx is MDX: decision records contain braces and angle brackets
  },
  i18n: { defaultLocale: 'en', locales: ['en'] },
  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          routeBasePath: '/',
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: `https://github.com/${owner}/${repo}/edit/main/website/`,
        },
        blog: false,
        theme: { customCss: require.resolve('./src/css/custom.css') },
      }),
    ],
  ],
  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: { respectPrefersColorScheme: true },
      navbar: {
        title: 'Lecture Lens',
        logo: { alt: 'Lecture Lens', src: 'img/logo.svg' },
        items: [
          { type: 'docSidebar', sidebarId: 'docs', position: 'left', label: 'Docs' },
          { to: '/project/roadmap', label: 'Roadmap', position: 'left' },
          { href: `https://github.com/${owner}/${repo}`, label: 'GitHub', position: 'right' },
        ],
      },
      footer: {
        style: 'dark',
        copyright:
          'Lecture Lens is independent and unaffiliated with any university, course provider, platform, or the makers of screenpipe. Apache-2.0.',
      },
      prism: { theme: themes.github, darkTheme: themes.dracula, additionalLanguages: ['bash', 'swift', 'json'] },
    }),
};

module.exports = config;
