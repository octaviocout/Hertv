const { strict: assert } = require("assert");
const { searchChannels, getAllChannels, getChannelById, getChannelsByCategory } = require("./channels");

// searchChannels: match by name
{
  const results = searchChannels("news");
  assert.equal(results.length, 1);
  assert.equal(results[0].name, "News 24");
}

// searchChannels: match by category
{
  const results = searchChannels("sports");
  assert.equal(results.length, 1);
  assert.equal(results[0].category, "sports");
}

// searchChannels: case-insensitive
{
  const results = searchChannels("MUSIC");
  assert.equal(results.length, 1);
  assert.equal(results[0].name, "Music TV");
}

// searchChannels: no match returns empty array
{
  const results = searchChannels("xyznonexistent");
  assert.equal(results.length, 0);
}

// searchChannels: partial name match
{
  const results = searchChannels("classic");
  assert.equal(results.length, 1);
  assert.equal(results[0].name, "Movie Classics");
}

// getAllChannels returns all 8 channels
{
  assert.equal(getAllChannels().length, 8);
}

// getChannelById: existing
{
  const ch = getChannelById(1);
  assert.equal(ch.name, "News 24");
}

// getChannelById: non-existing
{
  assert.equal(getChannelById(999), null);
}

// getChannelsByCategory
{
  const results = getChannelsByCategory("kids");
  assert.equal(results.length, 1);
  assert.equal(results[0].name, "Kids Zone");
}

console.log("All tests passed.");
