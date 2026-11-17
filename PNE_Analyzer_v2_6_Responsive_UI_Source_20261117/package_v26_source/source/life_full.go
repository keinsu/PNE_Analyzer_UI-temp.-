package main

import (
	"encoding/csv"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

type lifeFullPoint struct {
	Cycle              string  `json:"cycle"`
	Charge             float64 `json:"charge"`
	Discharge          float64 `json:"discharge"`
	ChargeSum          float64 `json:"chargeSum"`
	DischargeSum       float64 `json:"dischargeSum"`
	ChargeEnergy       float64 `json:"chargeEnergy"`
	DischargeEnergy    float64 `json:"dischargeEnergy"`
	DischargeEnergyRep float64 `json:"dischargeEnergyRep"`
	EnergyRetention    float64 `json:"energyRetention"`
	HasCharge          bool    `json:"hasCharge"`
	HasDischarge       bool    `json:"hasDischarge"`
	HasTemperature     bool    `json:"hasTemperature"`
	HasDischargeEnergy bool    `json:"hasDischargeEnergy"`
	CE                 float64 `json:"ce"`
	EE                 float64 `json:"ee"`
	Retention          float64 `json:"retention"`
	Temperature        float64 `json:"temperature"`
	Imp10              float64 `json:"imp10"`
	CVTime             float64 `json:"cvTime"`
	ChargeTime         float64 `json:"chargeTime"`
	DvDt               float64 `json:"dvdt"`
	ChargeSteps        int     `json:"chargeSteps"`
	DischargeSteps     int     `json:"dischargeSteps"`
	Warning            string  `json:"warning"`
}
type lifeFullSeries struct {
	Channel int             `json:"channel"`
	Name    string          `json:"name"`
	Points  []lifeFullPoint `json:"points"`
}

func validateLifeBase(all []analyzed, chs []int, baseCycle string) error {
	if strings.TrimSpace(baseCycle) == "" {
		return nil
	}
	for _, ch := range chs {
		if ch < 0 || ch >= len(all) {
			continue
		}
		found := false
		for _, s := range all[ch].segments {
			if s.row < 0 || (s.status != "MATCHED" && s.status != "REVIEW") {
				continue
			}
			r := all[ch].csvRows[s.row]
			typ := strings.ToLower(r.typ)
			if strings.TrimSpace(r.curCycle) == baseCycle && (strings.Contains(typ, "방전") || strings.Contains(typ, "discharge")) && (math.Abs(r.capacity) > 0 || math.Abs(s.lastRec.cap) > 0) {
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("기준 Cycle %s의 방전 Step이 채널 %s에 없습니다. 실제 있는 Cycle 번호를 입력하세요", baseCycle, all[ch].pair.id)
		}
	}
	return nil
}
func lifeFull(all []analyzed, chs []int, baseCycle string) []lifeFullSeries {
	out := []lifeFullSeries{}
	for _, ch := range chs {
		if ch < 0 || ch >= len(all) {
			continue
		}
		a := all[ch]
		m := map[string]*lifeFullPoint{}
		for _, s := range a.segments {
			if s.row < 0 || (s.status != "MATCHED" && s.status != "REVIEW") {
				continue
			}
			row := a.csvRows[s.row]
			cy := strings.TrimSpace(row.curCycle)
			if cy == "" {
				continue
			}
			p := m[cy]
			if p == nil {
				p = &lifeFullPoint{Cycle: cy}
				m[cy] = p
			}
			typ := strings.ToLower(row.typ)
			vq := math.Abs(row.capacity)
			if !row.hasCap {
				vq = math.Abs(s.lastRec.cap) / 1000
			}
			en := math.Abs(row.energy)
			if strings.Contains(typ, "충전") || strings.Contains(typ, "charge") {
				p.ChargeSteps++
				p.HasCharge = p.HasCharge || vq > 0
				p.ChargeSum += vq
				p.Charge = math.Max(p.Charge, vq)
				p.ChargeEnergy += en
				p.CVTime += math.Max(s.lastRec.cvTime, 0)
				p.ChargeTime += math.Max(s.lastRec.stepTime, 0)
			} else if strings.Contains(typ, "방전") || strings.Contains(typ, "discharge") {
				p.DischargeSteps++
				p.HasDischarge = p.HasDischarge || vq > 0
				p.DischargeSum += vq
				// The representative discharge energy follows the discharge
				// Step selected by maximum capacity, NOT the sum of pulses.
				if vq > p.Discharge || (vq == p.Discharge && en > p.DischargeEnergyRep) {
					p.Discharge = vq
					p.DischargeEnergyRep = en
				}
				p.DischargeEnergy += en
				p.HasDischargeEnergy = p.HasDischargeEnergy || en > 0
			} else if (strings.Contains(typ, "휴지") || strings.Contains(typ, "rest")) && s.lastRec.stepTime > 30 {
				slope := (s.lastRec.v - s.firstRec.v) / s.lastRec.stepTime * 3600
				if math.Abs(slope) > math.Abs(p.DvDt) {
					p.DvDt = slope
				} // mV/hour
			}
			if s.lastRec.temp >= -50 && s.lastRec.temp <= 140 && isFinite(s.lastRec.temp) {
				if !p.HasTemperature || s.lastRec.temp > p.Temperature {
					p.Temperature = s.lastRec.temp
				}
				p.HasTemperature = true
			}
			if s.lastRec.imp10 > 0 && s.lastRec.imp10 < 10000 && s.lastRec.imp10 > p.Imp10 {
				p.Imp10 = s.lastRec.imp10
			}
		}
		cycles := make([]string, 0, len(m))
		for c := range m {
			cycles = append(cycles, c)
		}
		sort.Slice(cycles, func(i, j int) bool {
			x, e := strconv.Atoi(cycles[i])
			y, f := strconv.Atoi(cycles[j])
			if e == nil && f == nil {
				return x < y
			}
			return cycles[i] < cycles[j]
		})
		ref := 0.0
		energyRef := 0.0
		if baseCycle != "" && m[baseCycle] != nil {
			ref = m[baseCycle].Discharge
			energyRef = m[baseCycle].DischargeEnergyRep
		}
		if ref <= 0 {
			for _, cy := range cycles {
				if m[cy].Discharge > 0 {
					ref = m[cy].Discharge
					if baseCycle == "" {
						energyRef = m[cy].DischargeEnergyRep
					}
					break
				}
			}
		}
		points := []lifeFullPoint{}
		for _, cy := range cycles {
			p := m[cy]
			if p.Charge > 0 && p.Discharge > 0 {
				p.CE = 100 * p.Discharge / p.Charge
			}
			if p.ChargeEnergy > 0 && p.DischargeEnergy > 0 {
				p.EE = 100 * p.DischargeEnergy / p.ChargeEnergy
			}
			if ref > 0 && p.Discharge > 0 {
				p.Retention = 100 * p.Discharge / ref
			}
			if energyRef > 0 && p.DischargeEnergyRep > 0 {
				p.EnergyRetention = 100 * p.DischargeEnergyRep / energyRef
			}
			if p.ChargeSteps > 1 || p.DischargeSteps > 1 {
				p.Warning = "복수 충/방전 Step: 대표값(max)과 합계(sum) 차이 확인"
			}
			points = append(points, *p)
		}
		out = append(out, lifeFullSeries{Channel: ch, Name: a.pair.id, Points: points})
	}
	return out
}
func exportLifeFull(path string, data []lifeFullSeries) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	f.Write([]byte{0xef, 0xbb, 0xbf})
	w := csv.NewWriter(f)
	w.Write([]string{"Source_File", "Cycle", "Charge_Representative_Ah", "Discharge_Representative_Ah", "Charge_Step_Sum_Ah", "Discharge_Step_Sum_Ah", "Charge_Energy_Wh", "Discharge_Energy_Wh", "Discharge_Energy_Representative_Wh", "Energy_Retention_Percent", "CE_Percent_Representative", "Energy_Efficiency_Percent", "Retention_Percent", "Temperature_Max_C", "Impedance10s_Max_mOhm", "CVTime_Total_s", "ChargeTime_Total_s", "Rest_dVdt_mV_per_hour", "ChargeStepCount", "DischargeStepCount", "Review"})
	for _, s := range data {
		for _, p := range s.Points {
			w.Write([]string{safeCell(s.Name), safeCell(p.Cycle), ff(p.Charge), ff(p.Discharge), ff(p.ChargeSum), ff(p.DischargeSum), ff(p.ChargeEnergy), ff(p.DischargeEnergy), ff(p.DischargeEnergyRep), ff(p.EnergyRetention), ff(p.CE), ff(p.EE), ff(p.Retention), ff(p.Temperature), ff(p.Imp10), ff(p.CVTime), ff(p.ChargeTime), ff(p.DvDt), strconv.Itoa(p.ChargeSteps), strconv.Itoa(p.DischargeSteps), safeCell(p.Warning)})
		}
	}
	w.Flush()
	return w.Error()
}
