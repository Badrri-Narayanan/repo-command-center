import { ApiClient } from "./api.js";
import { Dashboard } from "./dashboard.js";
import { RepoStore } from "./model.js";
import { Prefs } from "./util.js";

const token = document.querySelector('meta[name="rcc-token"]').content;
const prefs = new Prefs();

new Dashboard(document, { store: new RepoStore(prefs), api: new ApiClient(token), prefs }).start();
