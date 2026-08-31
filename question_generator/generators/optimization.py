from __future__ import annotations

import random

from .base import QuestionCategoryGenerator
from ..models import QuestionCandidate, QuestionTemplate
from ..scene import SceneData


class OptimizationQuestionGenerator(QuestionCategoryGenerator):
    """Reject the legacy single-Twin generator path for optimization tasks.

    Optimization labels require one context Twin and multiple candidate Twins,
    so the complete implementation lives in ``optimization_workflow`` and is
    invoked by ``python main.py questions -t optimization``.
    """

    def generate_candidate(
        self,
        scene: SceneData,
        template: QuestionTemplate,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        del scene, template, target_label, rng
        raise ValueError(
            "Optimization questions compare a context Twin with candidate "
            "Twins; run 'python main.py questions -t optimization'"
        )
