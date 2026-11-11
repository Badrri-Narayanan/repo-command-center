import { hostOf } from "./util.js";

const FORGES = [
  {
    match: "github", short: "GH", links: [
      ["Pull requests", "/pulls"],
      ["Actions", "/actions"],
      ["Open PR for this branch", branch => `/compare/${branch}?expand=1`],
    ],
  },
  {
    match: "gitlab", short: "GL", links: [
      ["Merge requests", "/-/merge_requests"],
      ["Pipelines", "/-/pipelines"],
      ["Open MR for this branch", branch => `/-/merge_requests/new?merge_request%5Bsource_branch%5D=${branch}`],
    ],
  },
  {
    match: "bitbucket", short: "BB", links: [
      ["Pull requests", "/pull-requests"],
      ["Pipelines", "/pipelines"],
      ["Open PR for this branch", branch => `/pull-requests/new?source=${branch}`],
    ],
  },
];
const UNKNOWN_FORGE = { short: "↗", links: [] };

export function forgeFor(webUrl) {
  const host = hostOf(webUrl);
  return FORGES.find(forge => host.includes(forge.match)) ?? UNKNOWN_FORGE;
}

/** Full URLs for the ⋯ menu. Branch-specific links are left out when there is no branch (detached HEAD). */
export function forgeLinks(webUrl, branch) {
  return forgeFor(webUrl).links
    .filter(([, path]) => typeof path === "string" || branch)
    .map(([label, path]) => ({ label, url: webUrl + (typeof path === "string" ? path : path(encodeURI(branch))) }));
}
