# Test Sample Module

A versatile synthetic signal and mock data generator for simulation, chart plotting, and pipeline validation.

---

## Technical Specifications

- **Inputs**:
  - `TRIGGER`: Programmatic trigger to generate and emit the configured sample.
  - `CMD_DICT`: Configuration dictionary to dynamically update parameters (`waveform`, `num_points`, `amplitude`, `frequency`, `offset`, `noise_level`, `sample_name`) and trigger emission.
- **Outputs**:
  - `Data` (Type: `SAMPLE`): Emits a standard `SAMPLE` packet (`name`, `uuid`, `x`, `y`, `action="select"`).

---

## Supported Waveforms

1. **Sine**: Sinusoidal wave ($y = A \sin(2\pi f t) + \text{offset}$).
2. **Cosine**: Cosine wave ($y = A \cos(2\pi f t) + \text{offset}$).
3. **Square**: Bipolar square wave ($y = A \cdot \text{sign}(\sin(2\pi f t)) + \text{offset}$).
4. **Sawtooth**: Linear ramp sawtooth wave.
5. **Triangle**: Symmetric triangular wave.
6. **Sigmoid**: Smooth logistic transition curve.
7. **Gaussian Peak**: Centered normal bell curve.
8. **Random**: Uniform random signal with adjustable amplitude.

---

## Adjustable Parameters

- **Points**: Total number of discrete points in the sample (range: 10 to 2000).
- **Amplitude**: Peak signal amplitude.
- **Frequency**: Number of complete wave cycles across the sample length.
- **Offset**: DC baseline shift.
- **Noise**: Standard deviation of additive Gaussian noise.
- **Name**: Custom label assigned to the emitted sample packet.

---

## Usage

- Configure desired waveform and parameters in the module window.
- Click **Emit Sample** to broadcast the sample downstream.
- Can be controlled or triggered programmatically via `TRIGGER` or `CMD_DICT`.
