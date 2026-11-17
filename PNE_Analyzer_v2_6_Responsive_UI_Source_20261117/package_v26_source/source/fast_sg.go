package main

import "math"

// sgFilterFast computes the same local polynomial intercept as sgFilter,
// but caches least-squares weights for repeated window positions. This avoids
// refitting 20,000 different matrices when a user requests 20,000 samples.
func sgFilterFast(y []float64, window, order int) []float64 {
	n := len(y)
	out := make([]float64, n)
	if window > n {
		window = n
		if window%2 == 0 {
			window--
		}
	}
	if window <= order || window < 3 {
		copy(out, y)
		return out
	}
	d := order + 1
	cached := map[int][]float64{}
	for i := range y {
		lo := i - window/2
		if lo < 0 {
			lo = 0
		}
		if lo+window > n {
			lo = n - window
		}
		offset := i - lo
		weights, ok := cached[offset]
		if !ok {
			gram := make([][]float64, d)
			for k := range gram {
				gram[k] = make([]float64, d+1)
			}
			norm := math.Max(1, float64(window-1)/2)
			for j := 0; j < window; j++ {
				x := (float64(j - offset)) / norm
				p := make([]float64, 2*order+1)
				p[0] = 1
				for k := 1; k < len(p); k++ {
					p[k] = p[k-1] * x
				}
				for k := 0; k < d; k++ {
					for m := 0; m < d; m++ {
						gram[k][m] += p[k+m]
					}
				}
			}
			gram[0][d] = 1
			// Solve (X'X) c = (1,0,..) for the constant-term extraction row.
			for k := 0; k < d; k++ {
				pivot := k
				for j := k + 1; j < d; j++ {
					if math.Abs(gram[j][k]) > math.Abs(gram[pivot][k]) {
						pivot = j
					}
				}
				gram[k], gram[pivot] = gram[pivot], gram[k]
				div := gram[k][k]
				if math.Abs(div) < 1e-15 {
					copy(out, y)
					return out
				}
				for j := k; j <= d; j++ {
					gram[k][j] /= div
				}
				for j := 0; j < d; j++ {
					if j == k {
						continue
					}
					v := gram[j][k]
					for m := k; m <= d; m++ {
						gram[j][m] -= v * gram[k][m]
					}
				}
			}
			weights = make([]float64, window)
			for j := range weights {
				x := (float64(j - offset)) / norm
				p := 1.0
				for k := 0; k < d; k++ {
					weights[j] += gram[k][d] * p
					p *= x
				}
			}
			cached[offset] = weights
		}
		sum := 0.0
		for j, w := range weights {
			sum += w * y[lo+j]
		}
		out[i] = sum
	}
	return out
}
