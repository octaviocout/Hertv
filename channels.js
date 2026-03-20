// In-memory channel data store
const channels = [
  { id: 1, name: "News 24", category: "news", language: "en", isLive: true },
  { id: 2, name: "Sports World", category: "sports", language: "en", isLive: true },
  { id: 3, name: "Movie Classics", category: "movies", language: "en", isLive: false },
  { id: 4, name: "Kids Zone", category: "kids", language: "en", isLive: true },
  { id: 5, name: "Documentary Plus", category: "documentary", language: "en", isLive: false },
  { id: 6, name: "Music TV", category: "music", language: "en", isLive: true },
  { id: 7, name: "Tech Talk", category: "tech", language: "en", isLive: false },
  { id: 8, name: "Comedy Central", category: "comedy", language: "en", isLive: true },
];

/**
 * Get all channels.
 * TODO: Add pagination support (page, limit query params)
 */
function getAllChannels() {
  return channels;
}

/**
 * Get a single channel by ID.
 */
function getChannelById(id) {
  return channels.find((ch) => ch.id === id) || null;
}

/**
 * Search channels by name or category.
 * Returns channels where the query matches the name or category (case-insensitive).
 */
function searchChannels(query) {
  const lower = query.toLowerCase();
  return channels.filter(
    (ch) =>
      ch.name.toLowerCase().includes(lower) ||
      ch.category.toLowerCase().includes(lower)
  );
}

/**
 * Get channels filtered by category.
 */
function getChannelsByCategory(category) {
  return channels.filter((ch) => ch.category === category);
}

module.exports = { getAllChannels, getChannelById, searchChannels, getChannelsByCategory };
