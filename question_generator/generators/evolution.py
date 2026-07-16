from __future__ import annotations

import random

from .base import QuestionCategoryGenerator
from ..models import QuestionCandidate, QuestionTemplate
from ..scene import SceneData


class EvolutionQuestionGenerator(QuestionCategoryGenerator):
    def generate_candidate(
        self,
        scene: SceneData,
        template: QuestionTemplate,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        del scene, template, target_label, rng
        raise NotImplementedError("Evolution question generation is reserved but not implemented yet")
