// Clicking the toolbar button opens the Studio in a new tab. No permissions are requested:
// the page reads only the folder the user picks.
chrome.action.onClicked.addListener(() => chrome.tabs.create({ url: chrome.runtime.getURL("index.html") }));
