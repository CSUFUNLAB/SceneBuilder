from __future__ import annotations

from abc import ABC, abstractmethod
import random

from ..models import QuestionCandidate, QuestionTemplate
from ..scene import SceneData


class QuestionCategoryGenerator(ABC):
    @abstractmethod
    def generate_candidate(
        self,
        scene: SceneData,
        template: QuestionTemplate,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        raise NotImplementedError
