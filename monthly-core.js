window.__YARA_MONTHLY__ = {
  nativeFetch: window.__YARA_NATIVE_FETCH__ || window.fetch.bind(window),
  cleanText: function (value) { return String(value == null ? "" : value).trim(); },
  validImage: function (value) {
    var raw = String(value == null ? "" : value).trim();
    var lower = raw.toLowerCase();
    if (!raw || lower === "[image_removed]" || lower === "[imagem_removida]" || lower === "[removed]") return false;
    return raw.indexOf("data:image/") === 0 || /^\/?images\//i.test(raw);
  },
  request: async function (url) {
    var response = await (window.__YARA_NATIVE_FETCH__ || window.fetch.bind(window))(url, { cache: "no-store" });
    var result = await response.json();
    if (!response.ok || result.ok === false) throw new Error(result.error || ("Servidor retornou " + response.status));
    return result;
  }
};
