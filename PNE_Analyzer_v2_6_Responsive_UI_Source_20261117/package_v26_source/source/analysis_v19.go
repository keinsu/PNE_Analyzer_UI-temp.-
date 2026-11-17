package main

import (
	"bufio"
	"encoding/csv"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

type lifePoint struct {
	Cycle        string  `json:"cycle"`
	Charge       float64 `json:"charge"`
	Discharge    float64 `json:"discharge"`
	CE           float64 `json:"ce"`
	Retention    float64 `json:"retention"`
	Imp10        float64 `json:"imp10"`
	HasCharge    bool    `json:"hasCharge"`
	HasDischarge bool    `json:"hasDischarge"`
}

func lifeSeries(a analyzed) []lifePoint {
	type agg struct {
		charge, discharge, imp float64
		hc, hd, hi             bool
	}
	m := map[string]*agg{}
	for _, seg := range a.segments {
		if (seg.status != "MATCHED" && seg.status != "REVIEW") || seg.row < 0 || seg.row >= len(a.csvRows) {
			continue
		}
		row := a.csvRows[seg.row]
		cy := strings.TrimSpace(row.curCycle)
		if cy == "" {
			continue
		}
		item := m[cy]
		if item == nil {
			item = &agg{}
			m[cy] = item
		}
		typ := strings.ToLower(row.typ)
		// Do not sum step capacities: multiple partial/pulse steps may exist.
		if strings.Contains(typ, "충전") || strings.Contains(typ, "charge") {
			q := math.Abs(row.capacity)
			if q > item.charge && row.hasCap {
				item.charge = q
				item.hc = true
			}
		} else if strings.Contains(typ, "방전") || strings.Contains(typ, "discharge") {
			q := math.Abs(row.capacity)
			if q > item.discharge && row.hasCap {
				item.discharge = q
				item.hd = true
			}
		}
		if seg.lastRec.imp10 > 0 && isFinite(seg.lastRec.imp10) && (!item.hi || seg.lastRec.imp10 > item.imp) {
			item.imp = seg.lastRec.imp10
			item.hi = true
		}
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		x, e := strconv.ParseFloat(keys[i], 64)
		y, f := strconv.ParseFloat(keys[j], 64)
		if e == nil && f == nil {
			return x < y
		}
		return keys[i] < keys[j]
	})
	base := 0.0
	for _, k := range keys {
		if m[k].hd && m[k].discharge > 0 {
			base = m[k].discharge
			break
		}
	}
	result := make([]lifePoint, 0, len(keys))
	for _, k := range keys {
		x := m[k]
		p := lifePoint{Cycle: k, Charge: x.charge, Discharge: x.discharge, Imp10: x.imp, HasCharge: x.hc, HasDischarge: x.hd}
		if x.hc && x.hd && x.charge > 0 {
			p.CE = 100 * x.discharge / x.charge
		}
		if x.hd && base > 0 {
			p.Retention = 100 * x.discharge / base
		}
		result = append(result, p)
	}
	return result
}

type dqPoint struct {
	Voltage float64 `json:"v"`
	Q       float64 `json:"q"`
	Raw     float64 `json:"raw"`
	Smooth  float64 `json:"smooth"`
}

func dqdvSeries(a analyzed, cycle, step, mode string, interval float64, window int) ([]dqPoint, error) {
	if interval < 0.0002 || interval > 0.02 {
		return nil, fmt.Errorf("전압 간격은 0.2~20mV 사이여야 합니다")
	}
	if window < 3 || window > 101 || window%2 == 0 {
		return nil, fmt.Errorf("SG window는 3~101 홀수여야 합니다")
	}
	segments := []segment{}
	for _, s := range a.segments {
		if s.row < 0 || (s.status != "MATCHED" && s.status != "REVIEW") {
			continue
		}
		r := a.csvRows[s.row]
		if cycle != "" && r.curCycle != cycle {
			continue
		}
		if step != "" && r.num != step {
			continue
		}
		if mode == "charge" && !(strings.Contains(r.typ, "충전") || strings.Contains(strings.ToLower(r.typ), "charge")) {
			continue
		}
		if mode == "discharge" && !(strings.Contains(r.typ, "방전") || strings.Contains(strings.ToLower(r.typ), "discharge")) {
			continue
		}
		segments = append(segments, s)
	}
	if len(segments) == 0 {
		return nil, fmt.Errorf("조건에 해당하는 충·방전 Step이 없습니다")
	}
	// Use a single selected step rather than concatenate step-reset capacity curves.
	longest := segments[0]
	for _, s := range segments {
		if s.count > longest.count {
			longest = s
		}
	}
	f, e := a.pair.cyc.open()
	if e != nil {
		return nil, e
	}
	defer f.Close()
	if _, e = io.CopyN(io.Discard, f, headerBytes+recordBytes*(longest.first-1)); e != nil {
		return nil, e
	}
	pts := make([][2]float64, 0, longest.count)
	b := make([]byte, recordBytes)
	for k := int64(0); k < longest.count; k++ {
		if _, e = io.ReadFull(f, b); e != nil {
			return nil, e
		}
		r := decodeRec(b)
		v, q := r.v/1000, r.cap/1000
		if isFinite(v) && isFinite(q) && v > 0 && v < 6 && math.Abs(q) < 10000 {
			pts = append(pts, [2]float64{v, q})
		}
	}
	if len(pts) < 10 {
		return nil, fmt.Errorf("유효한 전압·용량 포인트가 부족합니다")
	}
	// Determine voltage sweep direction and keep only monotonic advancing extrema.
	asc := pts[len(pts)-1][0] >= pts[0][0]
	clean := make([][2]float64, 0, len(pts))
	for _, p := range pts {
		if len(clean) == 0 {
			clean = append(clean, p)
			continue
		}
		prev := clean[len(clean)-1][0]
		if (asc && p[0] > prev+0.000001) || (!asc && p[0] < prev-0.000001) {
			clean = append(clean, p)
		}
	}
	if len(clean) < 10 {
		return nil, fmt.Errorf("전압이 단조 변화하는 CC 구간이 너무 짧습니다")
	}
	if !asc {
		for i, j := 0, len(clean)-1; i < j; i, j = i+1, j-1 {
			clean[i], clean[j] = clean[j], clean[i]
		}
	}
	lo, hi := clean[0][0], clean[len(clean)-1][0]
	count := int((hi - lo) / interval)
	if count < 12 {
		return nil, fmt.Errorf("선택 구간에 전압 보간 포인트가 부족합니다")
	}
	if count > 20000 {
		return nil, fmt.Errorf("전압 간격이 지나치게 촘촘합니다")
	}
	grid := make([]float64, count+1)
	index := 0
	for j := 0; j <= count; j++ {
		v := lo + float64(j)*interval
		for index+1 < len(clean)-1 && clean[index+1][0] < v {
			index++
		}
		p, q := clean[index], clean[index+1]
		den := q[0] - p[0]
		if den <= 0 {
			return nil, fmt.Errorf("중복 전압 레코드")
		}
		grid[j] = p[1] + (q[1]-p[1])*(v-p[0])/den
	}
	raw := make([]float64, len(grid))
	for i := range grid {
		if i == 0 {
			raw[i] = (grid[1] - grid[0]) / interval
		} else if i == len(grid)-1 {
			raw[i] = (grid[i] - grid[i-1]) / interval
		} else {
			raw[i] = (grid[i+1] - grid[i-1]) / (2 * interval)
		}
	}
	smooth := sgFilter(raw, window, 3)
	result := make([]dqPoint, len(raw))
	for i := range raw {
		result[i] = dqPoint{Voltage: lo + float64(i)*interval, Raw: raw[i], Smooth: smooth[i]}
	}
	return result, nil
}

// Local least-squares polynomial fitted at each point; edge windows shift and refit.
func sgFilter(y []float64, window, order int) []float64 {
	n := len(y)
	out := make([]float64, n)
	if window > n {
		window = n
		if window%2 == 0 {
			window--
		}
	}
	if window <= order {
		copy(out, y)
		return out
	}
	for i := range y {
		lo := i - window/2
		if lo < 0 {
			lo = 0
		}
		if lo+window > n {
			lo = n - window
		}
		d := order + 1
		a := make([][]float64, d)
		for k := range a {
			a[k] = make([]float64, d+1)
		}
		for j := 0; j < window; j++ {
			x := float64(lo + j - i)
			powers := make([]float64, 2*order+1)
			powers[0] = 1
			for t := 1; t < len(powers); t++ {
				powers[t] = powers[t-1] * x
			}
			for k := 0; k < d; k++ {
				for l := 0; l < d; l++ {
					a[k][l] += powers[k+l]
				}
				a[k][d] += powers[k] * y[lo+j]
			}
		}
		for k := 0; k < d; k++ {
			pivot := k
			for r := k + 1; r < d; r++ {
				if math.Abs(a[r][k]) > math.Abs(a[pivot][k]) {
					pivot = r
				}
			}
			a[k], a[pivot] = a[pivot], a[k]
			if math.Abs(a[k][k]) < 1e-12 {
				continue
			}
			p := a[k][k]
			for l := k; l <= d; l++ {
				a[k][l] /= p
			}
			for r := 0; r < d; r++ {
				if r == k {
					continue
				}
				v := a[r][k]
				for l := k; l <= d; l++ {
					a[r][l] -= v * a[k][l]
				}
			}
		}
		out[i] = a[0][d]
	}
	return out
}
func writeDQCSV(path string, points []dqPoint) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	b := bufio.NewWriter(f)
	defer b.Flush()
	b.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(b)
	w.Write([]string{"Voltage_V", "dQdV_raw_Ah_per_V", "dQdV_SG_Ah_per_V"})
	for _, p := range points {
		w.Write([]string{ff(p.Voltage), ff(p.Raw), ff(p.Smooth)})
	}
	w.Flush()
	return w.Error()
}
