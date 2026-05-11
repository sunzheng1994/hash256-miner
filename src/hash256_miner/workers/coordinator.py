"""Epoch polling + shared mining generation counter."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3
from web3.contract import Contract

from hash256_miner.chain import MiningState, read_mining_state

log = logging.getLogger(__name__)


@dataclass
class Coordinator:
    w3: Web3
    contract: Contract
    miner_address: str
    abi_path: Path
    poll_interval: float = 2.0
    stop: threading.Event = threading.Event()
    lock: threading.Lock = threading.Lock()
    generation: int = 0
    state: MiningState | None = None

    def refresh(self) -> MiningState:
        st = read_mining_state(
            self.w3,
            self.contract,
            self.miner_address,
            abi_path=self.abi_path,
        )
        with self.lock:
            old = self.state
            bump = old is None or old.epoch != st.epoch or old.difficulty != st.difficulty
            if bump:
                self.generation += 1
            self.state = st
        if bump and old is not None:
            log.info(
                "mining parameters updated: epoch %s -> %s, gen=%s",
                old.epoch,
                st.epoch,
                self.generation,
            )
        return st

    def current(self) -> MiningState:
        with self.lock:
            if self.state is None:
                raise RuntimeError("coordinator not initialized")
            return self.state

    def generation_id(self) -> int:
        with self.lock:
            return self.generation

    def epoch_loop(self) -> None:
        while not self.stop.wait(self.poll_interval):
            try:
                self.refresh()
            except Exception:
                log.exception("epoch poll failed")


def start_epoch_thread(coord: Coordinator) -> threading.Thread:
    t = threading.Thread(target=coord.epoch_loop, name="epoch-poller", daemon=True)
    t.start()
    return t
