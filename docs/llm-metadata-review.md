# LLM Metadata Review Specification

This document provides instructions for an LLM to review a q8020 experiment case output directory and assess whether it contains the data needed for reproducibility and analysis.

## Purpose

Given a case output directory, the reviewer should:
1. Identify what data is present
2. Assess completeness against the conceptual schema categories
3. Flag missing or incomplete data
4. Summarize the experiment for human review

## Conceptual Schema Categories

Every experiment should capture data in these high-level categories. The reviewer must assess each:

| Category | Question to Answer |
|----------|-------------------|
| **Experiment** | Who ran this, when, and what identifiers link it to the workflow? |
| **Case** | What problem was solved? Are the inputs fully specified? |
| **Code** | What algorithm ran? What was the software environment? |
| **Backend** | Where did it execute? What are the hardware/simulator characteristics? |
| **Artifacts** | How was the computation performed? Circuit details, timing? |
| **Exec Stats** | Did it succeed? How long did it take? |
| **Results** | What solutions were produced? Both classical and quantum? |
| **Analysis** | How good was the result? Fidelity, error metrics? |

## Review Process

### Step 1: Inventory the Directory

List all files in the case directory. Files may have varying names depending on case/code/backend, but look for patterns:
- JSON files containing metadata, results, analysis
- Text files containing stdout/stderr
- Any other artifacts (plots, circuit diagrams, etc.)

Note: File naming conventions may vary. Focus on content, not exact filenames.

### Step 2: Find the Rollup File

Look for a composite metadata file (often named `q8020_metadata_*.json` or similar) that aggregates data from multiple sources. This is the primary file to review.

If no rollup exists, review individual fragment files.

### Step 3: Assess Each Category

For each category, determine:
- **Present**: Data exists and appears complete
- **Partial**: Some data exists but key fields are missing
- **Absent**: No data found for this category
- **N/A**: Category not applicable (e.g., no backend info for classical-only runs)

#### Experiment
Look for:
- Experiment/case name
- Unique identifier (experiment_id, workflow_id)
- Timestamp (ISO 8601)
- User info (username, hostname)

#### Case
Look for problem definition. This varies by domain:
- **Linear systems (Ax=b)**: matrix, rhs vector, dimension, condition number
- **Optimization**: objective, constraints, initial parameters
- **CFD/FVM**: mesh, boundary conditions, physical parameters
- **Generic**: At minimum, a problem name and algorithm identifier

Ask: Could someone reproduce the problem setup from this data?

#### Code
Look for:
- Algorithm identifier
- Entry point (script path)
- Library versions (especially domain-specific packages like qiskit, numpy)
- Any algorithm-specific parameters (precision, iterations, ansatz config)

Ask: Could someone set up the same software environment?

#### Backend
Look for execution target information:

**Identification**:
- Backend name/identifier
- Vendor (ibm, etc.)
- Type (simulator, hardware)
- Whether noise model is applied

**Topology** (quantum backends):
- Number of qubits
- Coupling map (qubit connectivity)
- Basis gates (native gate set)

**Error characteristics** (noisy backends):
- Readout error rates per qubit
- Gate error rates (single-qubit and two-qubit)
- T1/T2 coherence times (if available)

Note: Backend info may be absent for:
- Ideal/statevector simulations
- Runs that failed before backend initialization
- Classical-only computations

#### Artifacts
Look for computational details:

**Circuit metrics (quantum codes)**:
- Pre-transpile: depth, num_qubits, gate_counts by type
- Post-transpile: depth, num_qubits, gate_counts in basis gates
- Optimization level used
- Transpilation wall time

**Timing breakdowns**:
- Circuit construction time
- Transpilation time
- Execution/backend time
- Total wall time
- Shots requested vs executed

**Execution info**:
- Backend status (DONE, ERROR, etc.)
- Any intermediate checkpoints or phase timings

This category varies significantly by code type.

#### Exec Stats
Look for:
- Start and end times
- Duration
- Exit code
- Success/failure status

#### Results
Look for solution data:

**Quantum linear solvers**:
- Classical solution (exact reference from np.linalg.solve or similar)
- Classical solution normalized (unit length)
- Quantum solution (raw amplitudes from measurement)
- Quantum solution normalized (unit length)

**Variational algorithms**:
- Optimal parameters found
- Final cost/objective value
- Optimization trajectory (if available)

**CFD codes**:
- Field data (pressure, velocity, temperature, mach, etc.)
- Solution at specific timesteps or steady state

Ask: Are the actual computational outputs present? Are both raw and normalized forms available for comparison?

#### Analysis
Look for quality metrics:
- Fidelity (quantum vs classical agreement)
- Error norms (L2, residual)
- Convergence information
- Any domain-specific quality measures

Note: If the run failed, look for error descriptions instead.

### Step 4: Check Data Provenance

Each piece of data should have a source indicator (`_source` field or similar):
- `"solver"` — written by the algorithm code
- `"sweep"` — written by the experiment harness
- `"stdout"` — parsed from output

Verify that critical data (results, analysis) comes from authoritative sources.

### Step 5: Detect Failures

Check for failure conditions:
- **Hard failure**: Non-zero exit code, missing solver outputs
- **Soft failure**: Exit code 0, but error fields present in results/analysis

If failed, assess whether failure information is adequate for debugging.

## Output Format

Produce a structured assessment:

```
## Experiment Summary
- Name: [case/experiment name]
- ID: [experiment_id]
- Timestamp: [when run]
- Status: [success/failure]

## Category Assessment

| Category | Status | Notes |
|----------|--------|-------|
| Experiment | [Present/Partial/Absent] | [brief note] |
| Case | [Present/Partial/Absent] | [brief note] |
| Code | [Present/Partial/Absent] | [brief note] |
| Backend | [Present/Partial/Absent/N/A] | [brief note] |
| Artifacts | [Present/Partial/Absent] | [brief note] |
| Exec Stats | [Present/Partial/Absent] | [brief note] |
| Results | [Present/Partial/Absent] | [brief note] |
| Analysis | [Present/Partial/Absent] | [brief note] |

## Key Findings
- [Most important observations]
- [Missing data that would affect reproducibility]
- [Any anomalies or concerns]

## Reproducibility Assessment
[Can this experiment be reproduced from the captured data? What's missing?]
```

## Special Cases

### Failed Runs
For runs that failed (non-zero exit code):
- Exec Stats should still be present
- Case and Code should be present (problem was defined, code attempted to run)
- Backend, Results, Analysis may be absent
- Look for error information in stderr or error fields

### Partial Data
Some codes may not emit all categories. Acceptable gaps:
- Backend absent for ideal simulations
- Artifacts minimal for simple codes
- Analysis absent if results are raw (analysis done externally)

Flag as concerns:
- Missing Case (can't know what problem was solved)
- Missing Results (no output to evaluate)
- Missing Code/library versions (can't reproduce environment)

### Domain Variations
Different problem domains have different critical fields:

**Quantum linear solvers (Ax=b)**:
- Critical: matrix, rhs, quantum_solution, fidelity
- Important: condition_number, backend noise model, circuit depth

**CFD/FVM codes**:
- Critical: mesh, boundary conditions, solution fields
- Important: convergence history, timesteps, solver parameters

**Variational algorithms (VQE, VQLS)**:
- Critical: ansatz config, optimizer, iteration count, final cost
- Important: optimization trajectory, parameter values

Adapt assessment based on the algorithm type identified in the Case/Code sections.
