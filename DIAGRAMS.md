# Diagrams

Centralized Mermaid diagrams for `prusaslicer-skew-fix`.

## README Diagrams

### 1) End-to-end processing flow

```mermaid
flowchart TD
  A["PrusaSlicer exports text .gcode"] --> B["Run skew_fix_ps.py as post-process"]
  B --> C["Validate input (text G-code only, reject binary)"]
  C --> D["Parse moves and modal state"]
  D --> E["Linearize G2/G3 arcs to G1 segments"]
  E --> F["Compute shear reference y_ref (auto or fixed)"]
  F --> G["Apply XY shear transform"]
  G --> H{"--recenter-to-bed enabled?"}
  H -- "No" --> I["Write rewritten G-code"]
  H -- "Yes" --> J["Compute in-bed extruding bounds"]
  J --> K["Compute shift (center or clamp)"]
  K --> I
```

### 2) XY skew transform model

```mermaid
flowchart LR
  A["Input point (x, y)"] --> B["k = tan(theta)"]
  B --> C["x' = x + (y - y_ref) * k"]
  A --> D["y' = y"]
  C --> E["Output point (x', y')"]
  D --> E
```

### 3) Recenter decision flow

```mermaid
flowchart TD
  A["Skewed toolpath produced"] --> B["Extract in-bed extruding bounds"]
  B --> C["Apply margin to bed limits"]
  C --> D{"Fits with current position?"}
  D -- "Yes" --> E["Shift dx=0, dy=0"]
  D -- "No" --> F{"Recenter mode"}
  F -- "center" --> G["Use allowable-interval midpoint"]
  F -- "clamp" --> H["Use minimum valid shift"]
  G --> I["Apply XY translation"]
  H --> I
  E --> J["Emit final G-code"]
  I --> J
```

### 4) Arc handling

```mermaid
flowchart TD
  A["Arc command (G2/G3)"] --> B["Linearize arc into short G1 segments"]
  B --> C["Apply shear to each segment endpoint"]
  C --> D["Write transformed G1 sequence"]
```

## DESIGN Diagrams

### 1) Modal state machine (parsing)

```mermaid
stateDiagram-v2
  [*] --> ABS_XY
  ABS_XY --> REL_XY: "G91"
  REL_XY --> ABS_XY: "G90"

  state "Extrusion Mode" as EMode {
    [*] --> ABS_E
    ABS_E --> REL_E: "M83"
    REL_E --> ABS_E: "M82"
  }

  state "Arc Center Mode" as IJMode {
    [*] --> REL_IJ
    REL_IJ --> ABS_IJ: "G90.1"
    ABS_IJ --> REL_IJ: "G91.1"
  }
```

### 2) Recenter interval math

```mermaid
flowchart TD
  A["Skewed in-bed extruding bounds: minx,maxx,miny,maxy"] --> B["Compute allowed dx interval"]
  B --> C["dx_lo = (bed_x_min + margin) - minx"]
  B --> D["dx_hi = (bed_x_max - margin) - maxx"]
  A --> E["Compute allowed dy interval"]
  E --> F["dy_lo = (bed_y_min + margin) - miny"]
  E --> G["dy_hi = (bed_y_max - margin) - maxy"]
  C --> H{"Intervals valid (with EPS)?"}
  D --> H
  F --> H
  G --> H
  H -- "No" --> I["Fail: cannot fit after skew"]
  H -- "Yes" --> J{"Mode"}
  J -- "center" --> K["dx,dy = midpoint of each interval"]
  J -- "clamp" --> L["dx,dy = minimum valid shift (prefer 0)"]
```

### 3) Arc linearization and E handling

```mermaid
flowchart TD
  A["Read arc G2/G3 with modal state"] --> B["Linearize to G1 points (0.20 mm, 5.0 degrees max)"]
  B --> C["Apply shear to each point"]
  C --> D{"Extrusion mode"}
  D -- "M82 absolute E" --> E["Emit cumulative E values on generated G1 lines"]
  D -- "M83 relative E" --> F["Distribute arc delta-E so segment E values sum to original"]
  E --> G["Update state to original arc endpoint"]
  F --> G
```

## MEASURING_SKEW Diagrams

### 1) Method selection decision tree

```mermaid
flowchart TD
  A["Need XY skew estimate"] --> B{"Can print Califlower v2?"}
  B -- "Yes" --> C["Use Califlower v2 result"]
  C --> D["Pass angle with --skew-deg"]
  B -- "No" --> E{"Can measure large square diagonals accurately?"}
  E -- "Yes" --> F["Use square method"]
  F --> G["Pass AC,BD,AD with --skew-from-square"]
  E -- "No" --> H{"Have rectangle diagonal/side measurements?"}
  H -- "Yes" --> I["Use rectangle method as estimate"]
  I --> J["Pass AC,BD,AD,AB with --skew-from-rectangle"]
  H -- "No" --> K["Use mechanical checks only as diagnostic"]
```

### 2) Measurement geometry to CLI mapping

```mermaid
flowchart TD
  A["Rectangle corners: A,B,C,D"] --> B["Diagonal AC"]
  A --> C["Diagonal BD"]
  A --> D["Side AD"]
  A --> E["Side AB"]
  B --> F["--skew-from-square AC,BD,AD"]
  C --> F
  D --> F
  B --> G["--skew-from-rectangle AC,BD,AD,AB"]
  C --> G
  D --> G
  E --> G
```

### 3) Measurement error sources and mitigation

```mermaid
flowchart LR
  A["Input measurements"] --> B{"Primary error source"}
  B -- "Caliper resolution/noise" --> C["Print larger test artifact"]
  B -- "Elephant foot / first-layer artifacts" --> D["Ignore bottom region or post-process edges"]
  B -- "Corner rounding / slicer compensation" --> E["Prefer Califlower v2 or repeat and average"]
  B -- "Single-run variance" --> F["Repeat measurements and average"]
  C --> G["More stable skew estimate"]
  D --> G
  E --> G
  F --> G
```
