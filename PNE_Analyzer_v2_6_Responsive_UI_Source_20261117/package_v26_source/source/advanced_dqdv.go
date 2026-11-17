package main

import (
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

type dqSettings struct {
	Channels    []int   `json:"channels"`
	Cycles      string  `json:"cycles"`
	Mode        string  `json:"mode"`
	Points      int     `json:"points"`
	Smooth      bool    `json:"smooth"`
	Window      int     `json:"window"`
	Order       int     `json:"order"`
	Sensitivity float64 `json:"sensitivity"`
	Step        string  `json:"step"`
}
type dqCurve struct {
	Channel   int       `json:"channel"`
	Name      string    `json:"name"`
	Cycle     string    `json:"cycle"`
	Direction string    `json:"direction"`
	Step      string    `json:"step"`
	Points    []dqPoint `json:"points"`
	Peaks     []dqPeak  `json:"peaks"`
	Error     string    `json:"error,omitempty"`
}
type dqPeak struct {
	Voltage float64 `json:"voltage"`
	Value   float64 `json:"value"`
	Index   int     `json:"index"`
}

func cycleSet(expr string, available []string) (map[string]bool, error) {
	valid := map[string]bool{}
	if strings.TrimSpace(expr) == "" {
		for _, x := range available {
			valid[x] = true
		}
		return valid, nil
	}
	for _, part := range strings.Split(expr, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		chunks := strings.SplitN(part, ":", 2)
		endpoints := strings.SplitN(chunks[0], "-", 2)
		if len(endpoints) == 1 {
			n, e := strconv.Atoi(strings.TrimSpace(endpoints[0]))
			if e != nil || n < 0 {
				return nil, fmt.Errorf("Cycle 표기 오류: %q", part)
			}
			valid[strconv.Itoa(n)] = true
			continue
		}
		start, e := strconv.Atoi(strings.TrimSpace(endpoints[0]))
		if e != nil {
			return nil, fmt.Errorf("Cycle 범위 시작 오류: %s", part)
		}
		end, e := strconv.Atoi(strings.TrimSpace(endpoints[1]))
		if e != nil || end < start || end-start > 100000 {
			return nil, fmt.Errorf("Cycle 범위 종료 오류: %s", part)
		}
		step := 1
		if len(chunks) == 2 {
			step, e = strconv.Atoi(strings.TrimSpace(chunks[1]))
			if e != nil || step <= 0 {
				return nil, fmt.Errorf("Cycle 간격 오류: %s", part)
			}
		}
		for n := start; n <= end; n += step {
			valid[strconv.Itoa(n)] = true
		}
	}
	return valid, nil
}
func readSegmentPoints(a analyzed, s segment) ([][2]float64, error) {
	rc, e := a.pair.cyc.open()
	if e != nil {
		return nil, e
	}
	defer rc.Close()
	skip := int64(headerBytes) + recordBytes*(s.first-1)
	if _, e = io.CopyN(io.Discard, rc, skip); e != nil {
		return nil, e
	}
	out := make([][2]float64, 0, s.count)
	b := make([]byte, recordBytes)
	for i := int64(0); i < s.count; i++ {
		if _, e = io.ReadFull(rc, b); e != nil {
			return nil, e
		}
		r := decodeRec(b)
		v, q := r.v/1000, r.cap/1000
		if isFinite(v) && isFinite(q) && v > 0.3 && v < 5.5 && math.Abs(q) < 1000 {
			out = append(out, [2]float64{v, q})
		}
	}
	return out, nil
}
func bestDQSegment(a analyzed, cycle, mode, step string) (segment, string, error) {
	var best segment
	selected := ""
	found := false
	for _, s := range a.segments {
		if s.row < 0 || (s.status != "MATCHED" && s.status != "REVIEW") {
			continue
		}
		r := a.csvRows[s.row]
		if r.curCycle != cycle {
			continue
		}
		if step != "" && r.num != step {
			continue
		}
		typ := strings.ToLower(r.typ)
		if mode == "charge" && !strings.Contains(typ, "충전") && !strings.Contains(typ, "charge") {
			continue
		}
		if mode == "discharge" && !strings.Contains(typ, "방전") && !strings.Contains(typ, "discharge") {
			continue
		}
		if !found || s.count > best.count {
			best = s
			selected = r.num
			found = true
		}
	}
	if !found {
		return segment{}, "", fmt.Errorf("Cycle %s %s 유효 Step 없음", cycle, mode)
	}
	return best, selected, nil
}
func computeDQAdvanced(pts [][2]float64, mode string, cfg dqSettings) ([]dqPoint, []dqPeak, error) {
	if cfg.Points < 50 || cfg.Points > 20000 {
		return nil, nil, errors.New("보간 포인트는 50~20,000 범위")
	}
	if cfg.Order < 1 || cfg.Order > 5 {
		return nil, nil, errors.New("SG 다항식 차수는 1~5 범위")
	}
	if cfg.Sensitivity <= 0 || cfg.Sensitivity > 1 {
		return nil, nil, errors.New("피크 민감도는 0~1 사이")
	}
	if len(pts) < 10 {
		return nil, nil, errors.New("유효 원본 포인트가 부족함")
	}
	// Voltage monotonic envelope helps exclude CV/plateau and cyclic backtracking.
	asc := pts[len(pts)-1][0] >= pts[0][0]
	envelope := make([][2]float64, 0, len(pts))
	for _, p := range pts {
		if len(envelope) == 0 {
			envelope = append(envelope, p)
			continue
		}
		last := envelope[len(envelope)-1][0]
		if (asc && p[0] > last+0.000002) || (!asc && p[0] < last-0.000002) {
			envelope = append(envelope, p)
		}
	}
	if len(envelope) < 10 {
		return nil, nil, errors.New("단조 전압 CC 데이터 부족")
	}
	// Average duplicate voltages within 5 µV buckets after sorting.
	sort.Slice(envelope, func(i, j int) bool { return envelope[i][0] < envelope[j][0] })
	clean := make([][2]float64, 0, len(envelope))
	for i := 0; i < len(envelope); {
		j := i + 1
		v, q := envelope[i][0], envelope[i][1]
		for j < len(envelope) && envelope[j][0]-envelope[i][0] < 0.000005 {
			v += envelope[j][0]
			q += envelope[j][1]
			j++
		}
		count := float64(j - i)
		clean = append(clean, [2]float64{v / count, q / count})
		i = j
	}
	lo, hi := clean[0][0], clean[len(clean)-1][0]
	if hi-lo < 0.015 {
		return nil, nil, errors.New("전압 범위가 15mV 미만")
	}
	n := cfg.Points
	dv := (hi - lo) / float64(n-1)
	voltage := make([]float64, n)
	quantity := make([]float64, n)
	j := 0
	for i := 0; i < n; i++ {
		v := lo + float64(i)*dv
		for j < len(clean)-2 && clean[j+1][0] < v {
			j++
		}
		p, q := clean[j], clean[j+1]
		f := (v - p[0]) / (q[0] - p[0])
		if f < 0 {
			f = 0
		}
		if f > 1 {
			f = 1
		}
		voltage[i] = v
		quantity[i] = p[1] + f*(q[1]-p[1])
	}
	window := cfg.Window
	if window == 0 {
		window = (n / 25) | 1
		if window < 11 {
			window = 11
		}
	}
	if window%2 == 0 || window <= cfg.Order || window > n {
		return nil, nil, errors.New("SG window는 포인트 수 이하의 홀수이며 다항식 차수보다 커야 함")
	}
	filtered := quantity
	if cfg.Smooth {
		filtered = sgFilterFast(quantity, window, cfg.Order)
	}
	raw := make([]float64, n)
	for i := range raw {
		switch i {
		case 0:
			raw[i] = (filtered[1] - filtered[0]) / dv
		case n - 1:
			raw[i] = (filtered[n-1] - filtered[n-2]) / dv
		default:
			raw[i] = (filtered[i+1] - filtered[i-1]) / (2 * dv)
		}
		if mode == "discharge" {
			raw[i] = -math.Abs(raw[i])
		}
	}
	smooth := raw
	if cfg.Smooth {
		smooth = sgFilterFast(raw, window, cfg.Order)
	}
	out := make([]dqPoint, n)
	for i := range out {
		out[i] = dqPoint{Voltage: voltage[i], Raw: raw[i], Smooth: smooth[i], Q: quantity[i]}
	}
	peaks := findDQPeaks(out, cfg.Sensitivity)
	return out, peaks, nil
}
func findDQPeaks(a []dqPoint, sens float64) []dqPeak {
	if len(a) < 10 {
		return nil
	}
	ylo, yhi := math.Inf(1), math.Inf(-1)
	for _, p := range a {
		if p.Smooth < ylo {
			ylo = p.Smooth
		}
		if p.Smooth > yhi {
			yhi = p.Smooth
		}
	}
	prominence := (yhi - ylo) * sens
	peaks := []dqPeak{}
	edge := int(float64(len(a)) * 0.02)
	if edge < 2 {
		edge = 2
	}
	for i := edge; i < len(a)-edge; i++ {
		here := a[i].Smooth
		// Discharge curves are negative; take local minima as physically prominent peaks.
		peak := here > a[i-1].Smooth && here >= a[i+1].Smooth
		if here < 0 {
			peak = here < a[i-1].Smooth && here <= a[i+1].Smooth
		}
		if !peak {
			continue
		}
		left, right := math.Abs(here-a[i-1].Smooth), math.Abs(here-a[i+1].Smooth)
		// Wide-window prominence compares a local ±2% neighborhood.
		radius := len(a) / 50
		if radius < 3 {
			radius = 3
		}
		lv, rv := a[max(0, i-radius)].Smooth, a[min(len(a)-1, i+radius)].Smooth
		delta := math.Max(math.Abs(here-lv), math.Abs(here-rv))
		if math.Max(delta, math.Max(left, right)) >= prominence {
			peaks = append(peaks, dqPeak{Voltage: a[i].Voltage, Value: here, Index: i})
		}
	}
	if len(peaks) > 100 {
		sort.Slice(peaks, func(i, j int) bool { return math.Abs(peaks[i].Value) > math.Abs(peaks[j].Value) })
		peaks = peaks[:100]
		sort.Slice(peaks, func(i, j int) bool { return peaks[i].Voltage < peaks[j].Voltage })
	}
	return peaks
}
func dqAdvancedCurves(all []analyzed, cfg dqSettings) ([]dqCurve, error) {
	if cfg.Points == 0 {
		cfg.Points = 500
	}
	if cfg.Order == 0 {
		cfg.Order = 3
	}
	if cfg.Sensitivity == 0 {
		cfg.Sensitivity = .05
	}
	if cfg.Mode != "charge" && cfg.Mode != "discharge" && cfg.Mode != "both" {
		return nil, errors.New("충전 또는 방전 선택 필요")
	}
	out := []dqCurve{}
	for _, ch := range cfg.Channels {
		if ch < 0 || ch >= len(all) {
			return nil, fmt.Errorf("채널 번호 범위 초과: %d", ch)
		}
		a := all[ch]
		cycles := map[string]bool{}
		for _, r := range a.csvRows {
			if r.curCycle != "" {
				cycles[r.curCycle] = true
			}
		}
		available := make([]string, 0, len(cycles))
		for c := range cycles {
			available = append(available, c)
		}
		selected, e := cycleSet(cfg.Cycles, available)
		if e != nil {
			return nil, e
		}
		ordered := make([]string, 0, len(selected))
		for c := range selected {
			if cycles[c] {
				ordered = append(ordered, c)
			}
		}
		sort.Slice(ordered, func(i, j int) bool { x, _ := strconv.Atoi(ordered[i]); y, _ := strconv.Atoi(ordered[j]); return x < y })
		if len(ordered) > 50 {
			return nil, errors.New("동시 표시 Cycle은 최대 50개")
		}
		for _, cy := range ordered {
			dirs := []string{cfg.Mode}
			if cfg.Mode == "both" {
				dirs = []string{"charge", "discharge"}
			}
			for _, mode := range dirs {
				curve := dqCurve{Channel: ch, Name: a.pair.id, Cycle: cy, Direction: mode}
				s, step, e := bestDQSegment(a, cy, mode, cfg.Step)
				curve.Step = step
				if e == nil {
					var pts [][2]float64
					pts, e = readSegmentPoints(a, s)
					if e == nil {
						curve.Points, curve.Peaks, e = computeDQAdvanced(pts, mode, cfg)
					}
				}
				if e != nil {
					curve.Error = e.Error()
				}
				out = append(out, curve)
			}
		}
	}
	if len(out) == 0 {
		return nil, errors.New("요청한 Cycle 데이터가 없습니다")
	}
	return out, nil
}
func saveDQAdvanced(path string, cs []dqCurve) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	f.Write([]byte{0xEF, 0xBB, 0xBF})
	w := csv.NewWriter(f)
	w.Write([]string{"Channel", "Cycle", "Direction", "StepNo", "Voltage_V", "dQdV_Raw_Ah_per_V", "dQdV_Smoothed_Ah_per_V", "Error"})
	for _, c := range cs {
		if len(c.Points) == 0 {
			w.Write([]string{safeCell(c.Name), safeCell(c.Cycle), c.Direction, safeCell(c.Step), "", "", "", safeCell(c.Error)})
			continue
		}
		for _, p := range c.Points {
			w.Write([]string{safeCell(c.Name), safeCell(c.Cycle), c.Direction, safeCell(c.Step), ff(p.Voltage), ff(p.Raw), ff(p.Smooth), ""})
		}
	}
	w.Flush()
	return w.Error()
}
