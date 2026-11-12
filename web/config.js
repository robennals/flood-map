// Where the website reads its map files from. If you host your own copy, point dataUrl at your
// bucket's public address (see README). Add ?data=local to the page address to use web/data/ instead,
// which `make build` fills for testing.
window.FLOOD_MAP_CONFIG = {
  dataUrl: "https://floodmap-data.ennals.org",
  region: "bay-area",
};
