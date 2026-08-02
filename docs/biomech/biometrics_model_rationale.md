# **Biomechanical Model: Metric Selection and Research Rationale** 

HX5 wearable system — primitives, composite indexes and temporal windows 

**Purpose.** This document records what the HX5 biomechanical model measures, and why each metric was selected. It is intended as a reference for the engineering team and as a statement of the evidence base underlying the design. Every design choice below is traceable either to published literature (referenced at the end) or to an explicitly stated engineering constraint. 

**Scope note.** The metrics described here are _proxies_ derived from limb-mounted inertial sensors. They are not laboratory measurements of joint load, and the system is a monitoring and research tool, not a diagnostic device. Section 7 sets out the known limitations in full. 

## **1. The problem being addressed** 

Musculoskeletal injury (MSKI) is the dominant threat to US Army readiness, and critically, it is not primarily traumatic. More than half of all active-component soldiers sustain at least one injury in a given year, and **overuse injuries comprise at least 70% of all injuries** among them. MSKIs account for **65% of medically nondeployable** active-component soldiers, with roughly 4% of the force unable to deploy at any given moment for this reason [1]. Independent estimates place the burden at around two million medical encounters and eight million limited-duty days per year [2]. 

This shapes the entire model. A system optimised only to catch dramatic acute events would miss the majority of the injury burden. The metric set below is therefore weighted toward **cumulative loading and fatigue drift** , with acute-mechanism signals retained but not treated as the primary target. 

## **2. Design principles** 

**Parsimony.** The trial dataset is small (days, not seasons; few subjects). With many variables relative to sample size, models overfit. The set was therefore held to four primitives rather than expanded to fill available storage — storage was never the binding constraint; statistical power is. 

**Orthogonality.** An inertial measurement unit reports exactly two physical quantities: linear acceleration and angular velocity. Every metric is a reduction of those two signals. Beyond roughly four or five primitives, additional metrics re-describe the same information rather than adding to it. The four selected were chosen to span four distinct constructs: **magnitude, dose, rotation and rate** . 

**Physical units.** All primitives are reported in SI units (m/s<sup>2</sup> , deg/s, m/s<sup>3</sup> ) with no display scaling applied inside the model. Presentation scaling is the responsibility of the user interface. This ensures stored values remain physically interpretable and comparable across software versions. 

## **3. The four primitives** 

Measured per leg, per processing window. All composite indexes are derived from these; no other raw signal enters the model. 

|**Primitive**|**Physical quantity**|**Unit**|**Construct**|
|---|---|---|---|
|**peak_tibial_accel**|Peak axial (bone-aligned) shank acceleration|m/s<sup>2</sup>|Impact magnitude|
|**cumulative_tibial_load**|Time-averaged absolute axial loading|m/s<sup>2</sup>|Cumulative dose|



Hippos Exoskeleton — Biomechanical Model Rationale 

Page 1 

|**Primitive**|**Physical quantity**|**Unit**|**Construct**|
|---|---|---|---|
|**peak_shank_gyro**|Peak resultant shank angular velocity|deg/s|Segment rotation|
|**max-jerk**|Peak time-derivative of axial acceleration|m/s<sup>3</sup>|Loading rate|



### **3.1 Peak axial tibial acceleration** 

The most extensively validated inertial primitive available. Peak tibial acceleration correlates with vertical loading rate of the ground reaction force, with reported correlations in the range r = 0.64–0.84 across multiple studies [3]. In injured runners, vertical tibial acceleration was associated with loading rates at r = 0.66–0.82 across footstrike patterns, and was the stronger surrogate compared with resultant acceleration [4]. It also serves as the system's tibial-stress signal, which is directly relevant given the Army's stress-fracture burden. 

**Why axial rather than resultant magnitude.** The earlier model computed the resultant magnitude, which is rotation-invariant and therefore blends vertical impact with lateral and fore-aft motion. The literature validates the _axial_ component specifically. The model now extracts the single accelerometer axis aligned with the bone. 

### **3.2 Cumulative tibial load (dose)** 

Peak-based metrics are, by construction, blind to exposure volume. A soldier taking two thousand moderate foot-strikes and one taking two hundred can register an identical peak while differing greatly in accumulated loading. Given that overuse accounts for at least 70% of Army injuries [1], a dose term is essential rather than optional. It is computed as the time-averaged absolute axial acceleration over the window, which is independent of sample rate — important because packet loss over the wireless link varies the sample count. Longer-horizon exposure is then accumulated by the temporal layer. 

### **3.3 Peak shank angular velocity** 

Linear acceleration alone cannot capture the rotational mechanism implicated in ligament injury. The reference risk factor is the external knee abduction moment: in a prospective cohort of 205 female athletes, knee abduction moment during a drop vertical jump predicted subsequent ACL injury status with **78% sensitivity and 73% specificity** [5]. Segment angular velocity measured at the shank is the closest quantity an inertial sensor can provide as a proxy for that frontal-plane loading. Resultant magnitude is used here (unlike acceleration) because segment rotation is genuinely three-dimensional. 

### **3.4 Peak jerk (loading rate)** 

Jerk is the time-derivative of acceleration — how _abruptly_ load arrives, as distinct from how large it is. Two landings may share a peak acceleration while differing substantially in the rate at which that peak is reached. Since loading rate rather than peak magnitude is the ground-reaction variable most consistently associated with running injury, a rate term is retained as an independent primitive. It is derived from the shank signal, so it is co-located with the impact primitive and describes the same event. 

Hippos Exoskeleton — Biomechanical Model Rationale 

Page 2 

## **4. Temporal windows** 

Four windows are defined: two retrospective averaging windows and two forward projection horizons. Each is a single configurable value in the model. 

|**Window**|**Length**|**Purpose**|**Basis**|
|---|---|---|---|
|**Past-1**|15 min|Acute fatigue drift|Within-session change emerges on a minutes<br>timescale|
|**Past-2**|6 hours|Cumulative session load|Longest exposure window populatable in a short<br>trial|
|**Future-1**|1 hour|Near-term forecast|Matches the immediate command decision|
|**Future-2**|6 hours|Rest-of-session forecast|Matches the session-planning decision|



### **4.1 Why not the acute:chronic workload ratio** 

The conventional framework compares a one-week acute load against a four-week chronic average. It was rejected for two reasons. First, it cannot be populated: a multi-week chronic window is undefined in a trial lasting days. Second, the method itself is contested — the ratio distorts when chronic load is low, findings on its predictive value are inconsistent, and published reviews have argued its limitations should discourage routine use. The windows above are therefore defined on timescales that the trial can actually fill and that map to decisions a commander can act on. 

### **4.2 Why two windows rather than more** 

A window earns inclusion only if it captures a physiologically distinct regime. Within a short trial there are two on the retrospective side: acute within-session fatigue (minutes) and accumulated session exposure (hours). A third would interpolate between them rather than add information. On the forward side, each additional horizon is another prediction requiring ground truth to validate; a short trial can barely validate one. Two and two is the point at which further windows become decorative. 

A shorter 'imminent' horizon of roughly 20–30 minutes was considered and deliberately excluded from the initial configuration. It is arguably the most actionable forecast for acute prevention, but it cannot be validated on a trial of this length. It remains a single-line configuration change if acute alerting becomes a stated objective. 

## **5. Composite indexes** 

Five indexes summarise the primitives into a single comparable scale, plus two derived composites and a cross-limb measure. 

|**Index**|**Definition**|
|---|---|
|**present_index**|Mean of the four normalised primitives for the current window|
|**past1_index**|Same, averaged across the 15-minute window|
|**past2_index**|Same, averaged across the 6-hour window|
|**future1_index**|Present value projected 1 hour ahead on the recent trend (_forecast, not measurement_)|
|**future2_index**|Present value projected 6 hours ahead (_forecast, not measurement_)|
|**impact_attenuation**|Proportion of axial shock absorbed between shank and thigh; reduced attenuation<br>indicates loss of control|



Hippos Exoskeleton — Biomechanical Model Rationale 

Page 3 

|**Index**|**Definition**|
|---|---|
|**acl_risk**|Weighted blend of normalised primitives, weighted toward angular velocity as the closest<br>available proxy for knee abduction moment|
|**fatigue**|Displacement of the present index from its own recent baseline|
|**asym_***|Left/right symmetry index per primitive; requires both limbs|



### **5.1 Normalisation** 

The four primitives occupy numeric ranges differing by roughly two orders of magnitude. Averaging raw values would make any composite index effectively a proxy for whichever primitive carries the largest numbers, silently discarding the other three. Each primitive is therefore mapped onto a 0–100 scale against its own reference range before it is combined. The reference ranges are configuration values requiring calibration against trial data. 

### **5.2 Interlimb asymmetry** 

Asymmetry is among the clearest available fatigue signatures: tibial load asymmetry has been observed to rise substantially over the course of a fatiguing session, and asymmetry is an independent injury risk factor in its own right. It is computed by a separate component because the two limbs' sensor clocks are independent, so the limbs must be aligned by arrival time rather than by device timestamp. 

### **5.3 Treatment of forecasts** 

The two forward indexes are projections computed at write time from the retrospective trend. They contain no future information. The projection is deliberately damped, because extrapolating a 15-minute trend across a one-hour horizon otherwise amplifies short-term noise into large apparent swings. Any interface presenting these values should distinguish them visually from measured quantities. 

Hippos Exoskeleton — Biomechanical Model Rationale 

Page 4 

## **6. Signal processing decisions** 

**Filter bandwidth.** A 50 Hz low-pass is applied. This was raised from an earlier 12 Hz setting after testing showed that a 12 Hz cutoff attenuates a simulated heel-strike peak by approximately 54% — removing the majority of the impact content the model exists to measure. Impact transients occupy roughly the 10–50 Hz band; a 50 Hz cutoff retains about 98% of peak amplitude while still suppressing sensor noise, and sits well below the Nyquist limit at the 640 Hz effective sample rate. 

**Sample-rate independence.** The dose primitive is computed as a mean rather than a sum. A sum scales with the number of samples received, so wireless packet loss would have silently reduced the reported loading dose. Under the mean formulation, packet loss costs precision rather than magnitude. 

**Rate monitoring.** Each window records both the nominal and the measured sample rate, derived from actual packet timestamps, allowing any anomalous reading to be checked against transmission loss for that interval. 

## **7. Known limitations** 

**Material caveat for load-carriage conditions.** A 2025 laboratory study reported that average vertical loading rate and tibial accelerometry were _not_ valid assessments of internal tibial compressive force during walking or running with military load carriage, finding only weak-to-moderate association between IMU-derived peak resultant acceleration and reference-standard tibial compression [6]. Since load carriage is central to Army activity, tibial acceleration should be treated as a measure of external impact exposure and **not** interpreted as internal bone load under rucksack conditions. 

**Proxies, not joint measurements.** Every value is derived from limb-mounted inertial sensors. The knee abduction moment referenced in Section 3.3 is a laboratory measurement; angular velocity is a correlate of it, not a substitute. 

**Step-level validity is weaker than session-level.** Tibial acceleration has been reported to correlate only negligibly with ankle joint contact force on a step-by-step basis when stride length varies, with usable correlations emerging only when averaged across multiple steps [7]. Window-averaged and past-window values should be treated as more trustworthy than any single peak. 

**Inter-individual variability.** The same absolute acceleration carries different meaning for different individuals. Per-subject baselining is expected to matter more than absolute thresholds. 

**Uncalibrated reference ranges.** The normalisation ranges and all display thresholds are provisional starting points, not validated cut-offs. They require calibration against collected trial data before any value is interpreted as clinically meaningful. 

**Forecasts are unvalidated.** The forward indexes have not been tested against observed outcomes. Establishing forecast accuracy requires pairing each prediction with the measured value at the corresponding later time, which the stored time series supports but which has not yet been performed. 

## **8. References** 

> [1] Molloy JM, Pendergrass TL, Lee IE, et al. Musculoskeletal Injuries and United States Army Readiness Part I: Overview of Injuries and their Strategic Impact. _Military Medicine_ , 2020;185(9-10):e1461. https://academic.oup.com/milmed/article/185/9-10/e1461/5805225 

- [2] Common Data Elements and Databases Essential for the Study of Musculoskeletal Injuries in Military Personnel. _Military Medicine_ , 2024;189(9-10):e2146. https://academic.oup.com/milmed/article/189/9-10/e2146/7678863 

- [3] Van den Berghe P, Six J, Gerlo J, et al. Tibial Acceleration-Based Prediction of Maximal Vertical Loading Rate During Overground Running: A Machine Learning Approach. _Frontiers in Bioengineering and Biotechnology_ , 2020;8:33. https://www.frontiersin.org/journals/bioengineering-and-biotechnology/articles/10.3389/fbioe.2020.00033/full 

Hippos Exoskeleton — Biomechanical Model Rationale 

Page 5 

- [4] Tibial Acceleration Measured from Wearable Sensors Is Associated with Loading Rates in Injured Runners. _PM&R_ , 2019. https://pubmed.ncbi.nlm.nih.gov/31671242/ 

- [5] Hewett TE, Myer GD, Ford KR, et al. Biomechanical measures of neuromuscular control and valgus loading of the knee predict anterior cruciate ligament injury risk in female athletes: a prospective study. _American Journal of Sports Medicine_ , 2005;33(4):492-501. https://pubmed.ncbi.nlm.nih.gov/15722287/ 

- [6] Average vertical loading rate and tibial accelerometry are not valid assessments of internal tibial loads when walking or running with or without load carriage: A cross-sectional laboratory study. _Journal of Sports Sciences_ , 2025. https://www.tandfonline.com/doi/full/10.1080/02640414.2025.2567781 

- [7] Tibial acceleration alone is not a valid surrogate measure of tibial load in response to stride length manipulation. _Journal of Sport and Health Science_ , 2024. https://www.sciencedirect.com/science/article/pii/S2095254624001340 

Prepared for Hippos Exoskeleton Ltd. Reference figures are quoted from the sources listed above; readers should consult the primary literature before relying on any value for clinical or operational decisions. This document describes a monitoring and research system and does not constitute medical advice. 

Hippos Exoskeleton — Biomechanical Model Rationale 

Page 6 

