const assert = require("node:assert");
const test = require("node:test");

const { parseArgs } = require("./generate.js");

test("missing required arguments are rejected", () => {
  assert.throws(() => parseArgs(["--prompt", "hello"]), /output/);
});
