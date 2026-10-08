# Parallel processing

Version 1.4 uses one centralized resource plan based on logical CPU count,
available memory, CUDA availability, GPU count, point count, and workflow size.
The chosen plan and measured stage times are saved in every processing report.

CPU projection divides disjoint point chunks among as many as 32 workers while
reserving capacity for decoding, I/O, the desktop, and CUDA submission. Frame
decoding and LAS reads use bounded prefetch queues so memory use does not grow
with a recording.

CUDA performs visibility, ordinary color assignment, and multi-frame fusion
candidate collection. Work is submitted on a non-default CUDA stream while the
CPU prepares the next bounded input. A CUDA error switches the active job to the
reference CPU implementation without publishing a partial result.

Separate-output flights are independent scheduled jobs. CPU jobs may colorize
concurrently. With one CUDA device, two flights may prepare simultaneously but
a device gate permits only one projection job to own the selected GPU. With
multiple CUDA devices, flights are assigned round-robin and one projection job
may run concurrently on each GPU.

Merged best-view processing is a deterministic map/reduce operation. Source
discovery, telemetry loading, coverage inspection, and per-flight RGB decoding
run concurrently. Each flight maps candidates onto the unchanged point indices
of the final geometry, then one reducer selects the globally strongest color and
writes the LAS once. Equal-quality candidates retain input-flight order, matching
the historical winner policy.

With best-view colorization, sufficient temporary disk space, and Illumination
Balancing Off, each merged-workflow flight independently maps its best RGB
observation onto the same immutable merged point indices. A final deterministic
reducer selects the globally strongest candidate and writes the supplied merged
geometry once. Candidate shards use approximately 16 bytes per merged point per
flight and are deleted automatically after a successful or failed run.

Multi-frame Fusion and Illumination Balancing retain the global observation stream because
their cross-frame or cross-flight evidence must remain shared to preserve output
quality. Resource selection and fallback reasons are recorded in the report.

Fusion Off preserves the existing best-observation path. Fusion On retains the
strongest observations on CUDA when available and performs final robust medoid
selection in bounded CPU chunks.
