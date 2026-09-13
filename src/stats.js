/**
 * Return the arithmetic mean of an array of numbers.
 *
 * @param {number[]} values
 * @returns {number}
 * @throws {RangeError} If `values` is empty.
 */
export function mean(values) {
  if (values.length === 0) {
    throw new RangeError("mean() requires at least one value");
  }

  let total = 0;
  for (const value of values) {
    total += value;
  }
  return total / values.length;
}

/**
 * Return the median of an array of numbers. The input array is not modified.
 *
 * @param {number[]} values
 * @returns {number}
 * @throws {RangeError} If `values` is empty.
 */
export function median(values) {
  if (values.length === 0) {
    throw new RangeError("median() requires at least one value");
  }

  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }
  return sorted[middle];
}
