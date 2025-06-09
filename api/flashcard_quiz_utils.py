import random
from typing import List, Dict

def generate_flashcard_from_key_concept(key_concept: Dict) -> Dict:
    """
    Generate a simple Q&A flashcard from a key concept dict.
    Q: "What is [concept_title]?"
    A: concept_explanation
    """
    return {
        "question": f"What is {key_concept['concept_title']}?",
        "answer": key_concept["concept_explanation"]
    }

def generate_mcq_from_key_concepts(key_concept: Dict, all_key_concepts: List[Dict], num_distractors: int = 2) -> Dict:
    """
    Generate a multiple-choice question from a key concept.
    The correct answer is the concept's explanation. Distractors are explanations from other key concepts.
    """
    correct_answer = key_concept["concept_explanation"]
    distractors = [kc["concept_explanation"] for kc in all_key_concepts if kc["id"] != key_concept["id"]]
    random.shuffle(distractors)
    distractors = distractors[:num_distractors]
    options = distractors + [correct_answer]
    random.shuffle(options)
    return {
        "question": f"Which of the following best describes '{key_concept['concept_title']}'?",
        "options": options,
        "correct_answer": correct_answer,
        "distractors": distractors
    }

def generate_true_false_from_key_concepts(key_concept: Dict, all_key_concepts: List[Dict]) -> Dict:
    """
    Generate a true/false question from a key concept.
    50% chance: show correct explanation, 50% chance: show explanation from another concept.
    """
    use_true = random.choice([True, False])
    if use_true or len(all_key_concepts) == 1:
        statement = key_concept["concept_explanation"]
        is_true = True
    else:
        # Pick a random explanation from another key concept
        distractors = [kc for kc in all_key_concepts if kc["id"] != key_concept["id"]]
        distractor = random.choice(distractors)
        statement = distractor["concept_explanation"]
        is_true = False
    return {
        "question": f"True or False: '{key_concept['concept_title']}' is defined as: {statement}",
        "correct_answer": "True" if is_true else "False",
        "distractors": ["False" if is_true else "True"]
    }
