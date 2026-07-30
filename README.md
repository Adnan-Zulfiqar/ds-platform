# DS Platform

A small collection of statistics helpers, written in plain JavaScript with no
runtime dependencies.

## Requirements

Node.js 18 or newer. The tests use the built-in `node:test` runner, so there is
nothing to `npm install`.

## Usage

```js
import { mean, median } from "./src/stats.js";

mean([1, 2, 3, 4]);    // 2.5
median([3, 1, 2]);     // 2
median([4, 1, 3, 2]);  // 2.5
```

Both functions take an array of numbers. `median` does not modify the array you
pass in. Passing an empty array throws a `RangeError`, since neither statistic
is defined for zero values.

## Running the tests

```bash
node --test
```

## Layout

| Path | What it holds |
|---|---|
| `src/stats.js` | The statistics helpers |
| `test/stats.test.js` | Tests for those helpers |
