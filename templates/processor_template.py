from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.processing_base import ProcessingBase


class Processor_Template(ProcessingBase):
    """
    A template for building multiprocessing background worker processes.
    Subclasses ProcessingBase to leverage process isolation, input queues,
    and parameter tuning capabilities.
    """

    def __init__(
        self,
        params: Optional[Dict[str, Any]] = None,
        *,
        buffer_size: int = 10,
        drop_policy: str = "drop_new",
        daemon: bool = True,
    ) -> None:
        """
        Initialize the processor process.
        """
        super().__init__(
            params=params,
            buffer_size=buffer_size,
            drop_policy=drop_policy,
            daemon=daemon,
        )

    def _process_data(self, data: Any, params: Dict[str, Any]) -> Any:
        """
        Actual data processing logic executed in the isolated worker process.

        Args:
            data: The incoming data packet from the submission queue.
            params: Dictionary containing current parameters/configuration.

        Returns:
            The processed result to be pushed to the out_queue.
        """
        # Simulated workload
        time.sleep(1.0)

        # Example processing using data and params:
        factor = params.get("multiplier", 1)
        if isinstance(data, (int, float)):
            return data * factor

        return True