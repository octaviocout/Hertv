const http = require("http");
const { getAllChannels, getChannelById, searchChannels, getChannelsByCategory } = require("./channels");

const PORT = process.env.PORT || 3000;

function parseQuery(url) {
  const queryStart = url.indexOf("?");
  if (queryStart === -1) return {};
  const params = {};
  url
    .slice(queryStart + 1)
    .split("&")
    .forEach((pair) => {
      const [key, value] = pair.split("=");
      if (key) params[decodeURIComponent(key)] = decodeURIComponent(value || "");
    });
  return params;
}

function sendJSON(res, status, data) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

const server = http.createServer((req, res) => {
  const urlPath = req.url.split("?")[0];
  const query = parseQuery(req.url);

  if (req.method !== "GET") {
    return sendJSON(res, 405, { error: "Method not allowed" });
  }

  // GET /channels
  if (urlPath === "/channels") {
    if (query.q) {
      return sendJSON(res, 200, searchChannels(query.q));
    }
    if (query.category) {
      return sendJSON(res, 200, getChannelsByCategory(query.category));
    }
    return sendJSON(res, 200, getAllChannels());
  }

  // GET /channels/:id
  const channelMatch = urlPath.match(/^\/channels\/(\d+)$/);
  if (channelMatch) {
    const id = parseInt(channelMatch[1], 10);
    const channel = getChannelById(id);
    if (!channel) {
      return sendJSON(res, 404, { error: "Channel not found" });
    }
    return sendJSON(res, 200, channel);
  }

  sendJSON(res, 404, { error: "Not found" });
});

server.listen(PORT, () => {
  console.log(`HerTV API running on port ${PORT}`);
});

module.exports = server;
