/**
 * Return the arithmetic mean of an array of numbers.
 *
 * @param {number[]} values
 * @returns {number}
 */
export function mean(values) {
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
 */
export function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);

  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }
  return sorted[middle];
}
