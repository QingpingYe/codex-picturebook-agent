#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const BASE_URL = (process.env.PICTUREBOOK_SFACAI_BASE_URL || "https://newapi.sfacai.com").replace(/\/+$/, "");
const MODEL = process.env.PICTUREBOOK_SFACAI_MODEL || "gpt-image-2";

function parseArgs(argv) {
  const args = {
    prompt: null,
    size: null,
    quality: null,
    output: null,
    refs: [],
    json: false,
    apiKey: null,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--prompt") args.prompt = argv[++i];
    else if (arg === "--size") args.size = argv[++i];
    else if (arg === "--quality") args.quality = argv[++i];
    else if (arg === "--output") args.output = argv[++i];
    else if (arg === "--api-key") args.apiKey = argv[++i];
    else if (arg === "--refs") {
      while (i + 1 < argv.length && !argv[i + 1].startsWith("--")) {
        args.refs.push(argv[++i]);
      }
    } else if (arg === "--json") args.json = true;
  }

  for (const field of ["prompt", "output", "size", "quality"]) {
    if (!args[field]) {
      throw new Error(`missing --${field}`);
    }
  }

  return args;
}

function resolveApiKey(args, env = process.env) {
  return args.apiKey || env.PICTUREBOOK_SFACAI_KEY || null;
}

function apiPath(referenceCount) {
  return referenceCount > 0 ? "/v1/images/edits" : "/v1/images/generations";
}

function metadataPath(outputPath) {
  return path.join(path.dirname(path.resolve(outputPath)), "_metadata.json");
}

function writeMetadata(outputPath, requestedSize, quality, promptText) {
  const metaPath = metadataPath(outputPath);
  let entries = [];
  if (fs.existsSync(metaPath)) {
    try {
      const parsed = JSON.parse(fs.readFileSync(metaPath, "utf8"));
      if (Array.isArray(parsed.entries)) entries = parsed.entries;
    } catch (_) {
      entries = [];
    }
  }

  entries.push({
    filename: path.basename(outputPath),
    requested_size: requestedSize,
    image_quality: quality,
    prompt: promptText,
    status: "generated",
  });

  fs.mkdirSync(path.dirname(metaPath), { recursive: true });
  fs.writeFileSync(metaPath, JSON.stringify({ entries }, null, 2), "utf8");
}

function imageDimensions(buffer, requestedSize) {
  if (buffer.length > 24 && buffer[0] === 0x89 && buffer[1] === 0x50) {
    return `${buffer.readUInt32BE(16)}x${buffer.readUInt32BE(20)}`;
  }
  if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < buffer.length) {
      if (buffer[offset] !== 0xff) {
        offset += 1;
        continue;
      }
      const marker = buffer[offset + 1];
      if (marker === 0xc0 || marker === 0xc1 || marker === 0xc2) {
        return `${buffer.readUInt16BE(offset + 7)}x${buffer.readUInt16BE(offset + 5)}`;
      }
      offset += 2 + buffer.readUInt16BE(offset + 2);
    }
  }
  return requestedSize;
}

async function requestImage(args, apiKey) {
  const endpoint = apiPath(args.refs.length);
  let body;
  if (args.refs.length === 0) {
    body = JSON.stringify({
      model: MODEL,
      prompt: args.prompt,
      size: args.size,
      quality: args.quality,
      n: 1,
    });
  } else {
    const form = new FormData();
    form.append("model", MODEL);
    form.append("prompt", args.prompt);
    form.append("size", args.size);
    form.append("quality", args.quality);
    form.append("n", "1");

    for (const ref of args.refs) {
      const refPath = path.resolve(ref);
      if (!fs.existsSync(refPath)) throw new Error(`reference image not found: ${ref}`);
      form.append("image", new Blob([fs.readFileSync(refPath)]), path.basename(refPath));
    }
    body = form;
  }

  const response = await fetch(`${BASE_URL}${endpoint}`, {
    method: "POST",
    headers: args.refs.length === 0
      ? { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" }
      : { Authorization: `Bearer ${apiKey}` },
    body,
  });

  if (!response.ok) {
    throw new Error(`image API returned HTTP ${response.status}`);
  }

  const payload = await response.json();
  const b64 = payload?.data?.[0]?.b64_json;
  if (!b64) throw new Error("image API did not return b64_json");
  return Buffer.from(b64, "base64");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const apiKey = resolveApiKey(args);
  if (!apiKey) throw new Error("missing API key: use --api-key or PICTUREBOOK_SFACAI_KEY");

  const buffer = await requestImage(args, apiKey);
  const outputPath = path.resolve(args.output);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, buffer);
  writeMetadata(outputPath, args.size, args.quality, args.prompt);

  const result = {
    output_path: outputPath,
    requested_size: args.size,
    actual_size: imageDimensions(buffer, args.size),
    status: "generated",
  };
  process.stdout.write(args.json ? `${JSON.stringify(result)}\n` : `${outputPath}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${JSON.stringify({ error: error.message })}\n`);
    process.exitCode = 1;
  });
}

module.exports = { apiPath, parseArgs, resolveApiKey };
