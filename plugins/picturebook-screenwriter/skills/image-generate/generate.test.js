const assert = require("node:assert");
const test = require("node:test");

const { apiPath, parseArgs } = require("./generate.js");

test("missing required arguments are rejected", () => {
  assert.throws(() => parseArgs(["--prompt", "hello"]), /output/);
});

test("text-only generation uses the generation endpoint", () => {
  assert.equal(apiPath(0), "/v1/images/generations");
});

test("reference-backed generation uses the edit endpoint", () => {
  assert.equal(apiPath(2), "/v1/images/edits");
});
