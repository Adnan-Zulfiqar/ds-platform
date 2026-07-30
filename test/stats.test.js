import test from "node:test";
import assert from "node:assert/strict";

import { mean, median } from "../src/stats.js";

test("mean averages a list of numbers", () => {
  assert.equal(mean([1, 2, 3, 4]), 2.5);
});

test("mean handles a single value", () => {
  assert.equal(mean([7]), 7);
});

test("median picks the middle value of an odd-length list", () => {
  assert.equal(median([3, 1, 2]), 2);
});

test("median averages the two middle values of an even-length list", () => {
  assert.equal(median([4, 1, 3, 2]), 2.5);
});

test("median leaves the caller's array untouched", () => {
  const values = [3, 1, 2];
  median(values);
  assert.deepEqual(values, [3, 1, 2]);
});

test("mean rejects an empty list", () => {
  assert.throws(() => mean([]), RangeError);
});

test("median rejects an empty list", () => {
  assert.throws(() => median([]), RangeError);
});
