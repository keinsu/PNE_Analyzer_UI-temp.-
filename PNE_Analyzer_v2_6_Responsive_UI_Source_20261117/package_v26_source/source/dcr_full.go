package main

import (
	"encoding/csv"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

type dcrConfig struct {
	Channels         []int     `json:"channels"`
	Times            []float64 `json:"times"`
	SettingSteps     string    `json:"settingSteps"`
	MinCurrentA      float64   `json:"minCurrentA"`
	MaxPulseSec      float64   `json:"maxPulseSec"`
	BalanceTolerance float64   `json:"balanceTolerance"`
}
type dcrSample struct{ Time, V, I, Q, Power float64 }
type dcrPoint struct {
	Time       float64 `json:"time"`
	Voltage    float64 `json:"voltage"`
	Current    float64 `json:"current"`
	Resistance float64 `json:"resistance"`
	Available  bool    `json:"available"`
}
type dcrPulse struct {
	ID                string     `json:"id"`
	Channel           int        `json:"channel"`
	Source            string     `json:"source"`
	Cycle             string     `json:"cycle"`
	Step              string     `json:"step"`
	Block             int        `json:"block"`
	SOC               string     `json:"soc"`
	Direction         string     `json:"direction"`
	Control           string     `json:"control"`
	CurrentA          float64    `json:"currentA"`
	V0                float64    `json:"v0"`
	V0Source          string     `json:"v0Source"`
	Duration          float64    `json:"duration"`
	CapacityMAh       float64    `json:"capacityMAh"`
	ReturnCapacityMAh float64    `json:"returnCapacityMAh"`
	ReturnStep        string     `json:"returnStep"`
	BalanceError      float64    `json:"balanceError"`
	Review            string     `json:"review"`
	Include           bool       `json:"include"`
	Values            []dcrPoint `json:"values"`
	// Selected pulse samples only; never raw exported to ChatGPT.
}
type dcrResult struct {
	Pulses []dcrPulse `json:"pulses"`
	Times  []float64  `json:"times"`
	Notes  []string   `json:"notes"`
}

func parseStepIDs(s string) (map[string]bool, error) {
	m := map[string]bool{}
	if strings.TrimSpace(s) == "" {
		return m, nil
	}
	for _, tok := range strings.Split(s, ",") {
		tok = strings.TrimSpace(tok)
		if tok == "" {
			continue
		}
		nums := strings.Split(tok, "-")
		if len(nums) > 2 {
			return nil, fmt.Errorf("StepNo 표현 오류: %q", tok)
		}
		left, e := strconv.Atoi(strings.TrimSpace(nums[0]))
		if e != nil || left < 0 {
			return nil, fmt.Errorf("StepNo 표현 오류: %q", tok)
		}
		right := left
		if len(nums) == 2 {
			right, e = strconv.Atoi(strings.TrimSpace(nums[1]))
			if e != nil || right < left || right-left > 10000 {
				return nil, fmt.Errorf("StepNo 범위 오류: %q", tok)
			}
		}
		for n := left; n <= right; n++ {
			m[strconv.Itoa(n)] = true
		}
	}
	return m, nil
}
func physicalStepCapacity(a analyzed, index int) float64 {
	s := a.segments[index]
	if s.row < 0 {
		return math.Abs(s.lastRec.cap)
	}
	r := a.csvRows[s.row]
	cap := math.Abs(s.lastRec.cap)
	t := s.lastRec.stepTime
	for j := index + 1; j < len(a.segments); j++ {
		x := a.segments[j]
		if x.row < 0 {
			break
		}
		y := a.csvRows[x.row]
		if y.num != r.num || y.curCycle != r.curCycle || x.firstRec.stepTime+1e-4 < t {
			break
		}
		cap = math.Max(cap, math.Abs(x.lastRec.cap))
		t = x.lastRec.stepTime
	}
	return cap
}
func isActiveStep(r stepRow) bool {
	t := strings.ToLower(r.typ)
	return strings.Contains(t, "충전") || strings.Contains(t, "방전") || strings.Contains(t, "charge") || strings.Contains(t, "discharge")
}
func directionOf(i float64) string {
	if i < 0 {
		return "방전"
	}
	return "충전"
}
func chooseCurrent(samples []dcrSample) (float64, bool) {
	if len(samples) == 0 {
		return 0, false
	}
	maxT := samples[len(samples)-1].Time
	arr := []float64{}
	for _, s := range samples {
		if s.Time >= maxT*.67 && math.Abs(s.I) > 20 && isFinite(s.I) {
			arr = append(arr, s.I)
		}
	}
	if len(arr) == 0 {
		for _, s := range samples {
			if math.Abs(s.I) > 20 {
				arr = append(arr, s.I)
			}
		}
	}
	if len(arr) == 0 {
		return 0, false
	}
	sort.Float64s(arr)
	median := arr[len(arr)/2]
	// CC vs CP discrimination requires enough samples and significant current variation.
	powerMean, currentMean := 0.0, 0.0
	for _, s := range samples {
		powerMean += math.Abs(s.Power)
		currentMean += math.Abs(s.I)
	}
	powerMean /= float64(len(samples))
	currentMean /= float64(len(samples))
	pVar, iVar := 0.0, 0.0
	for _, s := range samples {
		pVar += math.Pow(math.Abs(s.Power)-powerMean, 2)
		iVar += math.Pow(math.Abs(s.I)-currentMean, 2)
	}
	pVar = math.Sqrt(pVar/float64(len(samples))) / math.Max(powerMean, 1)
	iVar = math.Sqrt(iVar/float64(len(samples))) / math.Max(currentMean, 1)
	return median, powerMean > 0.1 && pVar < .02 && iVar > .03
}
func interpolateSample(samples []dcrSample, t float64) (dcrSample, bool) {
	if len(samples) == 0 || t < samples[0].Time-0.05 || t > samples[len(samples)-1].Time+0.05 {
		return dcrSample{}, false
	}
	i := sort.Search(len(samples), func(i int) bool { return samples[i].Time >= t })
	if i == 0 {
		return samples[0], true
	}
	if i >= len(samples) {
		return samples[len(samples)-1], true
	}
	a, b := samples[i-1], samples[i]
	if b.Time <= a.Time {
		return b, true
	}
	k := (t - a.Time) / (b.Time - a.Time)
	return dcrSample{Time: t, V: a.V + (b.V-a.V)*k, I: a.I + (b.I-a.I)*k, Q: a.Q + (b.Q-a.Q)*k, Power: a.Power + (b.Power-a.Power)*k}, true
}
func readDCRSamples(a analyzed, wanted map[int]bool) (map[int][]dcrSample, error) {
	rc, e := a.pair.cyc.open()
	if e != nil {
		return nil, e
	}
	defer rc.Close()
	if _, e = io.CopyN(io.Discard, rc, headerBytes); e != nil {
		return nil, e
	}
	res := map[int][]dcrSample{}
	segPos := 0
	b := make([]byte, recordBytes)
	for n := int64(1); n <= a.records; n++ {
		if _, e = io.ReadFull(rc, b); e != nil {
			return nil, e
		}
		for segPos < len(a.segments) && a.segments[segPos].last < n {
			segPos++
		}
		if segPos == len(a.segments) {
			break
		}
		if !wanted[segPos] || n < a.segments[segPos].first {
			continue
		}
		r := decodeRec(b)
		if !isFinite(r.stepTime) || !isFinite(r.v) || !isFinite(r.i) {
			continue
		}
		res[segPos] = append(res[segPos], dcrSample{Time: r.stepTime, V: r.v, I: r.i, Q: r.cap, Power: r.power})
	}
	return res, nil
}
func computeDCR(all []analyzed, cfg dcrConfig) (dcrResult, error) {
	result := dcrResult{Pulses: []dcrPulse{}, Times: cfg.Times, Notes: []string{}}
	if len(cfg.Times) == 0 {
		cfg.Times = []float64{1, 10, 30}
		result.Times = cfg.Times
	}
	if cfg.MinCurrentA <= 0 {
		cfg.MinCurrentA = .5
	}
	if cfg.MaxPulseSec <= 0 {
		cfg.MaxPulseSec = 130
	}
	if cfg.BalanceTolerance <= 0 {
		cfg.BalanceTolerance = .12
	}
	if len(cfg.Times) > 12 {
		return result, fmt.Errorf("DCR 시점은 최대 12개")
	}
	for _, t := range cfg.Times {
		if t < .05 || t > 600 {
			return result, fmt.Errorf("DCR 시점 %.2f초가 허용 범위 밖", t)
		}
	}
	excluded, e := parseStepIDs(cfg.SettingSteps)
	if e != nil {
		return result, e
	}
	for _, channel := range cfg.Channels {
		if channel < 0 || channel >= len(all) {
			return result, fmt.Errorf("채널 %d 범위 초과", channel)
		}
		a := all[channel]
		type active struct {
			pos int
			row stepRow
			cap float64
			amp float64
			sec float64
		}
		activeSteps := []active{}
		for i, s := range a.segments {
			if (s.status != "MATCHED" && s.status != "REVIEW") || s.row < 0 {
				continue
			}
			r := a.csvRows[s.row]
			if !isActiveStep(r) {
				continue
			}
			amp := math.Abs(s.lastRec.i) / 1000
			sec := s.lastRec.stepTime
			if math.Abs(amp) < .01 || sec <= 0 {
				continue
			}
			activeSteps = append(activeSteps, active{pos: i, row: r, cap: physicalStepCapacity(a, i), amp: amp, sec: sec})
		}
		usedReturn := map[int]bool{}
		selected := map[int]bool{}
		candidateOrder := []int{}
		// Each file/channel is an independent sequence; never bridge across channels.
		for j, act := range activeSteps {
			if usedReturn[j] || excluded[act.row.num] || act.sec < .09 || act.sec > cfg.MaxPulseSec || act.amp < cfg.MinCurrentA {
				continue
			}
			candidateOrder = append(candidateOrder, j)
			selected[act.pos] = true
			// Identify next reverse-direction physical step as return, based on capacity balance.
			for k := j + 1; k < len(activeSteps) && k <= j+3; k++ {
				other := activeSteps[k]
				if other.row.curCycle != act.row.curCycle || usedReturn[k] || excluded[other.row.num] {
					continue
				}
				if math.Signbit(a.segments[act.pos].lastRec.i) == math.Signbit(a.segments[other.pos].lastRec.i) {
					continue
				}
				err := math.Abs(other.cap-act.cap) / math.Max(act.cap, 1)
				if err <= .45 && other.cap > 0 {
					usedReturn[k] = true
					break
				}
				break
			}
		}
		samples, e := readDCRSamples(a, selected)
		if e != nil {
			return result, e
		}
		// Net charge/discharge capacity is the SOC grouping signal, not voltage.
		// A pulse and its balanced opposite-direction return cancel out, while
		// an intervening SOC-setting step moves this net balance substantially.
		netBefore := make([]float64, len(activeSteps))
		net, netCycle := 0.0, ""
		for k, act := range activeSteps {
			if netCycle != act.row.curCycle {
				net = 0
				netCycle = act.row.curCycle
			}
			netBefore[k] = net
			sign := 1.0
			if a.segments[act.pos].lastRec.i < 0 {
				sign = -1
			}
			net += sign * act.cap
		}
		block := 0
		prevJ := -1
		for _, j := range candidateOrder {
			act := activeSteps[j]
			seg := a.segments[act.pos]
			if prevJ < 0 || act.row.curCycle != activeSteps[prevJ].row.curCycle {
				block++
			} else {
				settingBetween := false
				for k := prevJ + 1; k < j; k++ {
					if excluded[activeSteps[k].row.num] {
						settingBetween = true
						break
					}
				}
				drift := math.Abs(netBefore[j] - netBefore[prevJ])
				threshold := math.Max(5, 3*math.Max(act.cap, activeSteps[prevJ].cap))
				if settingBetween || drift > threshold {
					block++
				}
			}
			prevJ = j
			p := dcrPulse{ID: fmt.Sprintf("CH%d_C%s_S%s_R%d", channel+1, act.row.curCycle, act.row.num, seg.first), Channel: channel, Source: a.pair.id, Cycle: act.row.curCycle, Step: act.row.num, Block: block, Direction: directionOf(seg.lastRec.i), Duration: act.sec, CapacityMAh: act.cap, Include: true, Values: []dcrPoint{}}
			ss := samples[act.pos]
			if len(ss) < 2 {
				p.Review = "유효 Raw 샘플 부족"
				result.Pulses = append(result.Pulses, p)
				continue
			}
			p.V0 = ss[0].V / 1000
			p.V0Source = "첫 펄스 유효값"
			foundZero := false
			for _, s := range ss {
				if math.Abs(s.Time) <= .015 {
					p.V0 = s.V / 1000
					p.V0Source = "원시 0.0초"
					foundZero = true
					break
				}
			}
			if !foundZero {
				for k := act.pos - 1; k >= 0; k-- {
					r := a.segments[k]
					if r.row < 0 {
						continue
					}
					if a.csvRows[r.row].curCycle != act.row.curCycle {
						break
					}
					if math.Abs(r.lastRec.i) < 30 && r.count > 0 && plausible(r.lastRec) {
						p.V0 = r.lastRec.v / 1000
						p.V0Source = "직전 Rest 종료"
						break
					}
					if math.Abs(r.lastRec.i) >= 30 {
						break
					}
				}
			}
			current, cp := chooseCurrent(ss)
			p.Control = "CC"
			if cp {
				p.Control = "CP"
			}
			currentA := math.Round((math.Abs(current)/1000)*100) / 100
			p.CurrentA = currentA
			if currentA == 0 {
				p.Review = "유효한 전류 없음"
			}
			for _, t := range cfg.Times {
				sample, ok := interpolateSample(ss, t)
				val := dcrPoint{Time: t}
				if ok {
					val.Voltage = sample.V / 1000
					usedA := currentA
					if cp {
						usedA = math.Round(math.Abs(sample.I)/10) / 100
					}
					val.Current = usedA
					if usedA > 0 {
						val.Resistance = math.Abs(p.V0*1000-sample.V) / usedA
						val.Available = true
					}
				}
				p.Values = append(p.Values, val)
			}
			foundReturn := false
			for k := j + 1; k < len(activeSteps) && k <= j+3; k++ {
				other := activeSteps[k]
				if other.row.curCycle != act.row.curCycle {
					break
				}
				if !usedReturn[k] {
					continue
				}
				p.ReturnStep = other.row.num
				p.ReturnCapacityMAh = other.cap
				p.BalanceError = 100 * math.Abs(other.cap-act.cap) / math.Max(act.cap, 1)
				foundReturn = true
				break
			}
			if !foundReturn {
				p.Review = appendReview(p.Review, "SOC return 미검출")
			} else if p.BalanceError > 100*cfg.BalanceTolerance {
				p.Review = appendReview(p.Review, "return 용량 불균형")
			}
			if len(ss) > 1 && ss[0].Time > 0.05 {
				p.Review = appendReview(p.Review, "0초 원시점 없음")
			}
			result.Pulses = append(result.Pulses, p)
		}
		if len(candidateOrder) == 0 {
			result.Notes = append(result.Notes, a.pair.id+": pulse 후보 없음 — 시험 종류/최대 pulse 시간·전류 기준 확인")
		}
	}
	if len(cfg.SettingSteps) == 0 {
		result.Notes = append(result.Notes, "SOC setting StepNo가 지정되지 않아 자동 SOC block은 임시 그룹입니다. SOC 묶음과 SOC%를 검토 화면에서 수정하세요.")
	}
	result.Notes = append(result.Notes, "SOC grouping은 전압이 아닌 용량 반환 일치와 설정 Step 경계를 기반으로 합니다. 불균형 펄스는 자동 삭제하지 않습니다.")
	return result, nil
}
func appendReview(a, b string) string {
	if a == "" {
		return b
	}
	return a + "; " + b
}
func exportDCRRawCSV(path string, items []dcrPulse) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	f.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(f)
	w.Write([]string{"Source_File", "Cycle", "StepNo", "SOC_Block", "SOC_Percent", "Included", "Direction", "Control", "Current_A_2dp", "V0_V", "V0Source", "PulseDuration_s", "PulseCapacity_mAh", "ReturnStepNo", "ReturnCapacity_mAh", "ReturnBalanceError_Percent", "Review", "Time_s", "Voltage_V", "Current_A", "DCIR_mOhm"})
	for _, p := range items {
		for _, v := range p.Values {
			val := ""
			if v.Available {
				val = ff(v.Resistance)
			}
			w.Write([]string{safeCell(p.Source), safeCell(p.Cycle), safeCell(p.Step), strconv.Itoa(p.Block), safeCell(p.SOC), strconv.FormatBool(p.Include), p.Direction, p.Control, ff(p.CurrentA), ff(p.V0), p.V0Source, ff(p.Duration), ff(p.CapacityMAh), safeCell(p.ReturnStep), ff(p.ReturnCapacityMAh), ff(p.BalanceError), safeCell(p.Review), ff(v.Time), ff(v.Voltage), ff(v.Current), val})
		}
	}
	w.Flush()
	return w.Error()
}
