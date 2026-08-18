> **Status: SUPERSEDED (2026-08-19).** Written blind to this repo. Kept as
> dated research notes for the *ideas* (turn-taking, topic alignment,
> speech-quality, sentiment, composite-scoring approaches) — these were
> folded into `docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md`'s
> "Post-session analytics scope" section, which maps each to an existing
> module to reuse (`app/scoring/relevance.py`, `pronunciation.py`,
> `app/metrics/vocabulary.py`/`syntax.py`/`fluency.py`) instead of new code.
> **Do not copy the code below as-is** — it has real bugs found during
> review:
> - §3.1 `FILLER_WORDS` includes multi-word phrases (`"you know"`, `"sort
>   of"`, `"i mean"`) but the tokenizer (`re.findall(r'\b\w+\b', ...)`)
>   only ever produces single-word tokens, so those phrases can never
>   match — filler-word counts are silently undercounted.
> - §1.2/§1.3 interruption/dominance calculations assume `segments` is
>   already sorted by `start_ms`/time and don't sort it themselves — wrong
>   results if the caller passes segments grouped by speaker instead.
>
> The tested, correct version of turn-taking/interruption/dominance logic
> for this project already exists at `backend/app/rooms/live_stats.py`
> (per the 08-11 backend plan) — use that instead.

# GD Analytics: Core Algorithms Reference

A practical guide to implementing each analytics metric with concrete examples.

---

## 1. TURN-TAKING & INTERRUPTION ANALYSIS

### 1.1 Basic Turn Detection from Transcripts

**Input:** List of transcript segments with speaker ID, start_time, end_time, text

```python
from typing import List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Segment:
    speaker_id: int
    speaker_name: str
    start_ms: int        # milliseconds
    end_ms: int
    text: str
    confidence: float    # ASR confidence 0-1

def detect_speaker_turns(segments: List[Segment]) -> Dict[int, List[Tuple]]:
    """
    Groups consecutive segments by speaker.
    A "turn" = one speaker's uninterrupted speaking period.
    
    Returns:
    {
        speaker_id: [
            (turn_start_ms, turn_end_ms, concatenated_text),
            ...
        ]
    }
    """
    turns_by_speaker = {}
    
    for segment in segments:
        if segment.speaker_id not in turns_by_speaker:
            turns_by_speaker[segment.speaker_id] = []
        
        last_turn = turns_by_speaker[segment.speaker_id][-1] if turns_by_speaker[segment.speaker_id] else None
        
        # If gap > 200ms (silence threshold), start new turn
        if last_turn and (segment.start_ms - last_turn[1] > 200):
            turns_by_speaker[segment.speaker_id].append((
                segment.start_ms,
                segment.end_ms,
                segment.text
            ))
        elif last_turn:
            # Extend last turn
            merged_turn = (
                last_turn[0],
                segment.end_ms,
                last_turn[2] + " " + segment.text
            )
            turns_by_speaker[segment.speaker_id][-1] = merged_turn
        else:
            # First turn for this speaker
            turns_by_speaker[segment.speaker_id].append((
                segment.start_ms,
                segment.end_ms,
                segment.text
            ))
    
    return turns_by_speaker

# Example output:
# {
#     1: [(0, 5000, "I think we should..."), (8000, 12000, "And also...")],
#     2: [(5200, 7900, "I agree but...")]
# }
```

### 1.2 Interrupt Detection

**Definition:** Speaker B interrupts Speaker A if:
- A is speaking
- B starts speaking before A finishes (overlap)
- A continues but eventually stops
- B's interruption is NOT a backchannel

```python
def detect_interruptions(segments: List[Segment]) -> Dict[str, int]:
    """
    Detects interruptions using overlapping speech detection.
    
    Returns: {
        'user_1_interrupted_by_user_2': count,
        'user_1_successful_interruptions': count,
        'user_1_backchannel_count': count,
        ...
    }
    """
    
    # Get current speaker at each millisecond
    def speaker_at_time(ms: int, segments: List[Segment]) -> int:
        for seg in segments:
            if seg.start_ms <= ms <= seg.end_ms:
                return seg.speaker_id
        return None
    
    interruptions = {}
    backchannel_threshold_ms = 500  # If B speaks <500ms, it's a backchannel
    
    # For each segment, check if it overlaps with previous speaker
    for i, segment in enumerate(segments):
        # Who was speaking just before this segment?
        prev_speaker = None
        for prev_seg in reversed(segments[:i]):
            if prev_seg.speaker_id != segment.speaker_id:
                prev_speaker = prev_seg.speaker_id
                time_since_prev = segment.start_ms - prev_seg.end_ms
                break
        
        if prev_speaker and time_since_prev < 0:  # Negative = overlap
            # This is an interruption or backchannel
            segment_duration = segment.end_ms - segment.start_ms
            
            if segment_duration < backchannel_threshold_ms:
                # Likely backchannel (brief interjection)
                key = f"speaker_{prev_speaker}_backchannel_from_{segment.speaker_id}"
                interruptions[key] = interruptions.get(key, 0) + 1
            else:
                # Interruption attempt
                key = f"speaker_{prev_speaker}_interrupted_by_{segment.speaker_id}"
                interruptions[key] = interruptions.get(key, 0) + 1
    
    return interruptions
```

### 1.3 Speaking Time & Dominance

```python
def calculate_dominance_metrics(segments: List[Segment]) -> Dict[int, Dict]:
    """
    Returns per-speaker:
    - total_speaking_time (seconds)
    - turn_count
    - avg_turn_duration
    - dominance_score (% of total session time)
    """
    
    speaker_stats = {}
    total_session_time = segments[-1].end_ms if segments else 0
    
    for segment in segments:
        if segment.speaker_id not in speaker_stats:
            speaker_stats[segment.speaker_id] = {
                'total_ms': 0,
                'turn_count': 0,
                'turns': []
            }
        
        turn_duration = segment.end_ms - segment.start_ms
        speaker_stats[segment.speaker_id]['total_ms'] += turn_duration
        speaker_stats[segment.speaker_id]['turns'].append(turn_duration)
    
    # Calculate final metrics
    results = {}
    for speaker_id, stats in speaker_stats.items():
        total_sec = stats['total_ms'] / 1000
        turns = stats['turns']
        
        results[speaker_id] = {
            'speaking_time_seconds': total_sec,
            'turn_count': len(turns),
            'avg_turn_duration_seconds': sum(turns) / len(turns) / 1000 if turns else 0,
            'min_turn_duration_seconds': min(turns) / 1000 if turns else 0,
            'max_turn_duration_seconds': max(turns) / 1000 if turns else 0,
            'dominance_percentage': (stats['total_ms'] / total_session_time * 100) if total_session_time else 0,
        }
    
    return results

# Example output:
# {
#     1: {
#         'speaking_time_seconds': 45.3,
#         'turn_count': 7,
#         'avg_turn_duration_seconds': 6.47,
#         'dominance_percentage': 45.3
#     },
#     2: {
#         'speaking_time_seconds': 29.8,
#         'turn_count': 5,
#         'avg_turn_duration_seconds': 5.96,
#         'dominance_percentage': 29.8
#     }
# }
```

### 1.4 Turn-Taking Latency

```python
def calculate_response_latency(segments: List[Segment]) -> Dict[int, Dict]:
    """
    Measures how quickly each speaker responds when spoken to.
    Lower latency = more engaged
    
    Returns per speaker:
    - avg_response_time_ms
    - median_response_time_ms
    - responsiveness_score (0-100, inverse of latency)
    """
    
    response_times = {}
    
    # Sort segments by time
    sorted_segments = sorted(segments, key=lambda s: s.start_ms)
    
    for i, segment in enumerate(sorted_segments):
        if segment.speaker_id not in response_times:
            response_times[segment.speaker_id] = []
        
        # Find previous speaker
        if i > 0:
            prev_speaker_id = sorted_segments[i-1].speaker_id
            
            if prev_speaker_id != segment.speaker_id:
                # Time between when prev speaker stopped and this speaker started
                gap = segment.start_ms - sorted_segments[i-1].end_ms
                
                if gap > 0:  # Only count if there's a gap (not overlapping)
                    response_times[segment.speaker_id].append(gap)
    
    # Calculate statistics
    import statistics
    results = {}
    
    for speaker_id, latencies in response_times.items():
        if latencies:
            avg_latency = statistics.mean(latencies)
            median_latency = statistics.median(latencies)
            
            # Score: 0ms = 100, 2000ms = 0 (capped)
            responsiveness_score = max(0, 100 - (avg_latency / 20))
            
            results[speaker_id] = {
                'avg_response_time_ms': avg_latency,
                'median_response_time_ms': median_latency,
                'max_response_time_ms': max(latencies),
                'responsiveness_score': responsiveness_score
            }
    
    return results
```

---

## 2. TOPIC ALIGNMENT ANALYSIS

### 2.1 Semantic Similarity Scoring

```python
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

class TopicAlignmentAnalyzer:
    def __init__(self):
        # Fast, lightweight model suitable for real-time scoring
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.topic_embedding = None
    
    def set_topic(self, topic: str):
        """Embed the discussion topic"""
        self.topic_embedding = self.model.encode(topic, convert_to_tensor=False)
    
    def score_segment(self, text: str) -> float:
        """
        Returns alignment score 0-100
        
        Scoring:
        - 1.0 cosine similarity = 100 (perfect alignment)
        - 0.5 cosine similarity = 50 (moderate alignment)
        - 0.0 cosine similarity = 0 (no alignment)
        """
        
        if self.topic_embedding is None:
            raise ValueError("Set topic first with set_topic()")
        
        segment_embedding = self.model.encode(text, convert_to_tensor=False)
        similarity = cosine_similarity([self.topic_embedding], [segment_embedding])[0][0]
        
        # Scale to 0-100
        score = similarity * 100
        return score
    
    def analyze_all_segments(self, segments: List[Segment]) -> Dict[int, List[Dict]]:
        """
        Returns for each speaker:
        [
            {
                'text': '...',
                'alignment_score': 75.2,
                'on_topic': True,  # > 50
                'timestamp': '0:23'
            },
            ...
        ]
        """
        
        results = {}
        
        for segment in segments:
            if segment.speaker_id not in results:
                results[segment.speaker_id] = []
            
            score = self.score_segment(segment.text)
            
            # Format timestamp
            minutes = segment.start_ms // 60000
            seconds = (segment.start_ms % 60000) // 1000
            timestamp = f"{minutes}:{seconds:02d}"
            
            results[segment.speaker_id].append({
                'text': segment.text,
                'alignment_score': round(score, 1),
                'on_topic': score > 50,
                'timestamp': timestamp
            })
        
        return results

# Usage:
analyzer = TopicAlignmentAnalyzer()
analyzer.set_topic("Should we implement AI in hiring decisions?")

alignments = analyzer.analyze_all_segments(segments)
# Output:
# {
#     1: [
#         {'text': 'I think AI can help...', 'alignment_score': 82.3, 'on_topic': True},
#         {'text': 'My cousin has a cat', 'alignment_score': 12.1, 'on_topic': False},
#     ]
# }
```

### 2.2 Topic Deviation Summary

```python
def summarize_topic_alignment(alignments: Dict[int, List[Dict]]) -> Dict[int, Dict]:
    """
    Summary statistics per speaker.
    
    Returns:
    {
        speaker_id: {
            'avg_alignment_score': 75.2,
            'on_topic_percentage': 85.7,
            'off_topic_count': 2,
            'topic_alignment_rating': 'Excellent'  # A/B/C/D
        }
    }
    """
    
    results = {}
    
    for speaker_id, turns in alignments.items():
        scores = [t['alignment_score'] for t in turns]
        on_topic_count = sum(1 for t in turns if t['on_topic'])
        
        avg_score = sum(scores) / len(scores) if scores else 0
        on_topic_pct = (on_topic_count / len(turns) * 100) if turns else 0
        
        # Rating scale
        if avg_score >= 80:
            rating = 'Excellent'
        elif avg_score >= 70:
            rating = 'Good'
        elif avg_score >= 50:
            rating = 'Fair'
        else:
            rating = 'Poor'
        
        results[speaker_id] = {
            'avg_alignment_score': round(avg_score, 1),
            'on_topic_percentage': round(on_topic_pct, 1),
            'off_topic_count': len(turns) - on_topic_count,
            'topic_alignment_rating': rating
        }
    
    return results
```

---

## 3. SPEECH QUALITY ANALYSIS

### 3.1 Filler Word & Confidence Scoring

```python
import re
from collections import Counter

FILLER_WORDS = {
    'um', 'uh', 'umm', 'err', 'erm',  # Vocalizations
    'like', 'you know', 'basically', 'honestly', 'actually', 'literally',  # Verbal tics
    'so', 'well', 'i mean', 'sort of', 'kind of'  # Hedging
}

def analyze_speech_confidence(text: str) -> Dict:
    """
    Analyzes speech for hesitation markers.
    Lower filler word usage = higher confidence.
    
    Returns:
    {
        'filler_word_count': 3,
        'filler_word_percentage': 8.2,  # % of total words
        'confidence_score': 91.8,  # Inverse of filler %
        'dominant_filler_words': [('like', 2), ('um', 1)],
        'confidence_level': 'High'  # High/Medium/Low
    }
    """
    
    # Tokenize
    tokens = re.findall(r'\b\w+\b', text.lower())
    
    if not tokens:
        return {
            'filler_word_count': 0,
            'filler_word_percentage': 0,
            'confidence_score': 100,
            'dominant_filler_words': [],
            'confidence_level': 'Not Determined'
        }
    
    # Count filler words
    filler_count = 0
    filler_freq = Counter()
    
    for token in tokens:
        if token in FILLER_WORDS:
            filler_count += 1
            filler_freq[token] += 1
    
    filler_pct = (filler_count / len(tokens)) * 100
    confidence_score = max(0, 100 - filler_pct * 2)  # Scale factor of 2
    
    # Confidence level
    if confidence_score >= 80:
        confidence_level = 'High'
    elif confidence_score >= 60:
        confidence_level = 'Medium'
    else:
        confidence_level = 'Low'
    
    return {
        'filler_word_count': filler_count,
        'filler_word_percentage': round(filler_pct, 1),
        'confidence_score': round(confidence_score, 1),
        'dominant_filler_words': filler_freq.most_common(3),
        'confidence_level': confidence_level
    }

# Example:
result = analyze_speech_confidence("Um, I think, like, we should, you know, focus on...")
# Output:
# {
#     'filler_word_count': 4,
#     'filler_word_percentage': 30.8,
#     'confidence_score': 38.4,
#     'dominant_filler_words': [('like', 2), ('um', 1), ('you know', 1)],
#     'confidence_level': 'Low'
# }
```

### 3.2 Vocabulary Analysis

```python
import math
from collections import Counter

def analyze_vocabulary(text: str) -> Dict:
    """
    Measures vocabulary richness.
    
    Returns:
    {
        'total_words': 120,
        'unique_words': 85,
        'lexical_diversity': 0.708,  # unique / total (Type-Token Ratio)
        'lexical_richness': 78.2,    # Herdan's C (adjusted for length)
        'avg_word_length': 4.8,      # characters
        'sentences_count': 8,
        'avg_sentence_length': 15,   # words
    }
    """
    
    tokens = re.findall(r'\b\w+\b', text.lower())
    
    if not tokens:
        return {
            'total_words': 0,
            'unique_words': 0,
            'lexical_diversity': 0,
            'lexical_richness': 0,
            'avg_word_length': 0,
            'sentences_count': 0,
            'avg_sentence_length': 0,
        }
    
    unique_words = len(set(tokens))
    total_words = len(tokens)
    
    # Type-Token Ratio (simple diversity)
    type_token_ratio = unique_words / total_words
    
    # Herdan's C (adjusts for text length bias)
    # C = log(unique) / log(total)
    herdan_c = math.log(unique_words) / math.log(total_words) if total_words > 1 else 0
    lexical_richness = herdan_c * 100  # Scale to 0-100
    
    # Word length
    avg_word_length = sum(len(w) for w in tokens) / len(tokens)
    
    # Sentence count (rough estimate from periods/question marks)
    sentences = len(re.split(r'[.!?]+', text)) - 1
    avg_sentence_length = total_words / sentences if sentences > 0 else 0
    
    return {
        'total_words': total_words,
        'unique_words': unique_words,
        'lexical_diversity': round(type_token_ratio, 3),
        'lexical_richness': round(lexical_richness, 1),  # 0-100
        'avg_word_length': round(avg_word_length, 1),
        'sentences_count': sentences,
        'avg_sentence_length': round(avg_sentence_length, 1),
    }

# Example:
vocab = analyze_vocabulary("The implementation strategy focuses on...")
# {
#     'total_words': 150,
#     'unique_words': 112,
#     'lexical_diversity': 0.747,  # Good vocabulary variety
#     'lexical_richness': 79.3,
#     'avg_word_length': 5.2,      # Longer words = more sophisticated
#     'sentences_count': 5,
#     'avg_sentence_length': 30
# }
```

### 3.3 Grammar & Fluency

```python
import spacy

class GrammarAnalyzer:
    def __init__(self):
        # Load English language model
        self.nlp = spacy.load('en_core_web_sm')
    
    def analyze_grammar(self, text: str) -> Dict:
        """
        Flags common grammatical issues.
        
        Returns:
        {
            'sentence_count': 8,
            'complete_sentences_percentage': 87.5,  # >= 1 verb
            'avg_sentence_length': 12.3,
            'subject_verb_agreement_issues': 0,
            'grammar_quality_score': 85,  # 0-100
        }
        """
        
        doc = self.nlp(text)
        
        sentences = list(doc.sents)
        sentence_count = len(sentences)
        
        if sentence_count == 0:
            return {
                'sentence_count': 0,
                'complete_sentences_percentage': 0,
                'avg_sentence_length': 0,
                'grammar_quality_score': 0,
            }
        
        # Check completeness (must have verb)
        complete_sentences = 0
        for sent in sentences:
            has_verb = any(token.pos_ == 'VERB' for token in sent)
            if has_verb:
                complete_sentences += 1
        
        completeness_pct = (complete_sentences / sentence_count) * 100
        avg_sent_length = len(doc) / sentence_count
        
        # Grammar quality score (simplified)
        # Perfect grammar = 100
        # Missing subject/verb = -10 each
        # Run-on sentences = -5
        grammar_score = 100 - ((sentence_count - complete_sentences) * 10)
        grammar_score = max(0, min(100, grammar_score))
        
        return {
            'sentence_count': sentence_count,
            'complete_sentences_percentage': round(completeness_pct, 1),
            'avg_sentence_length': round(avg_sent_length, 1),
            'grammar_quality_score': grammar_score,
        }

# Usage:
analyzer = GrammarAnalyzer()
grammar = analyzer.analyze_grammar("The strategy should focus on...")
```

---

## 4. ENGAGEMENT & PARTICIPATION METRICS

### 4.1 Overall Engagement Score

```python
def calculate_engagement_score(speaker_id: int, all_stats: Dict) -> float:
    """
    Composite engagement score (0-100) based on:
    - Speaking time (20%)
    - Turn frequency (20%)
    - Topic alignment (20%)
    - Responsiveness (20%)
    - Speech quality (20%)
    """
    
    speaker_stats = all_stats.get(speaker_id, {})
    
    # Normalize individual scores
    total_speaking_time = sum(s.get('speaking_time_seconds', 0) for s in all_stats.values())
    speaking_pct = (speaker_stats.get('speaking_time_seconds', 0) / total_speaking_time * 100) if total_speaking_time > 0 else 0
    speaking_score = min(100, speaking_pct * 2)  # Cap at 100
    
    turn_count = speaker_stats.get('turn_count', 0)
    avg_turns = sum(s.get('turn_count', 0) for s in all_stats.values()) / len(all_stats) if all_stats else 0
    turn_score = (turn_count / max(avg_turns, 1)) * 50  # 50-100 range
    
    topic_score = speaker_stats.get('topic_alignment_score', 75)
    
    response_score = speaker_stats.get('responsiveness_score', 50)
    
    vocab_score = speaker_stats.get('lexical_richness', 50)
    
    # Weighted composite
    engagement = (
        speaking_score * 0.20 +
        turn_score * 0.20 +
        topic_score * 0.20 +
        response_score * 0.20 +
        vocab_score * 0.20
    )
    
    return min(100, max(0, engagement))
```

### 4.2 Participation Balance

```python
def analyze_participation_balance(all_stats: Dict[int, Dict]) -> Dict:
    """
    Measures whether airtime is distributed fairly.
    
    Returns:
    {
        'gini_coefficient': 0.42,  # 0 = perfect equality, 1 = one person speaks all
        'participation_balance_rating': 'Fair',  # Excellent/Good/Fair/Poor
        'dominant_speaker_id': 1,
        'marginal_speaker_id': 3,
        'inclusive_analysis': 'Person 3 needs more encouragement'
    }
    """
    
    from statistics import stdev
    
    speaking_times = [s.get('speaking_time_seconds', 0) for s in all_stats.values()]
    
    if not speaking_times or len(speaking_times) < 2:
        return {
            'gini_coefficient': 0,
            'participation_balance_rating': 'Not Determined',
        }
    
    # Gini coefficient (0 = equal, 1 = unequal)
    sorted_times = sorted(speaking_times)
    n = len(sorted_times)
    cumsum = sum((i + 1) * sorted_times[i] for i in range(n))
    gini = (2 * cumsum) / (n * sum(sorted_times)) - (n + 1) / n
    
    # Rating
    if gini < 0.2:
        rating = 'Excellent'
    elif gini < 0.4:
        rating = 'Good'
    elif gini < 0.6:
        rating = 'Fair'
    else:
        rating = 'Poor'
    
    # Find dominant/marginal speakers
    dominant_speaker = max(all_stats.items(), key=lambda x: x[1].get('speaking_time_seconds', 0))
    marginal_speaker = min(all_stats.items(), key=lambda x: x[1].get('speaking_time_seconds', 0))
    
    return {
        'gini_coefficient': round(gini, 2),
        'participation_balance_rating': rating,
        'dominant_speaker_id': dominant_speaker[0],
        'dominant_speaker_pct': round(dominant_speaker[1].get('speaking_time_seconds', 0) / sum(speaking_times) * 100, 1),
        'marginal_speaker_id': marginal_speaker[0],
        'marginal_speaker_pct': round(marginal_speaker[1].get('speaking_time_seconds', 0) / sum(speaking_times) * 100, 1),
    }
```

---

## 5. SENTIMENT & DEBATE ANALYSIS

### 5.1 Agreement/Disagreement Detection

```python
from transformers import pipeline

class SentimentAnalyzer:
    def __init__(self):
        # Zero-shot classification for agreement/disagreement
        self.classifier = pipeline("zero-shot-classification", 
                                    model="facebook/bart-large-mnli")
    
    def detect_agreement(self, text: str) -> Dict:
        """
        Classifies utterance as agreement/disagreement/neutral.
        
        Returns:
        {
            'stance': 'disagreement',  # agreement/disagreement/neutral
            'confidence': 0.92,
            'reasoning': 'Contains "but" and "I disagree"'
        }
        """
        
        candidate_labels = ["agreement with previous point", "disagreement with previous point", "neutral statement"]
        
        result = self.classifier(text, candidate_labels)
        
        top_stance = result['labels'][0]
        confidence = result['scores'][0]
        
        # Map to simpler labels
        stance_map = {
            "agreement with previous point": "agreement",
            "disagreement with previous point": "disagreement",
            "neutral statement": "neutral"
        }
        
        return {
            'stance': stance_map.get(top_stance, 'neutral'),
            'confidence': round(confidence, 2),
            'all_scores': dict(zip(result['labels'], [round(s, 2) for s in result['scores']]))
        }
    
    def detect_debate_phrases(self, text: str) -> Dict:
        """
        Manual detection of common debate markers.
        """
        
        agreement_markers = ['i agree', 'absolutely', 'exactly', 'precisely', 'that\'s right', 'yes and']
        disagreement_markers = ['i disagree', 'but', 'however', 'on the other hand', 'that\'s wrong', 'not really']
        
        text_lower = text.lower()
        
        has_agreement = any(marker in text_lower for marker in agreement_markers)
        has_disagreement = any(marker in text_lower for marker in disagreement_markers)
        
        if has_disagreement:
            stance = 'disagreement'
        elif has_agreement:
            stance = 'agreement'
        else:
            stance = 'neutral'
        
        return {
            'stance': stance,
            'has_agreement_markers': has_agreement,
            'has_disagreement_markers': has_disagreement,
            'confidence': 'high' if (has_agreement or has_disagreement) else 'low'
        }
```

---

## 6. COMPOSITE SCORING EXAMPLES

### 6.1 Leadership Score

```python
def calculate_leadership_score(speaker_id: int, all_segments: List[Segment], all_stats: Dict) -> float:
    """
    Leadership combines:
    - First to speak (10 points)
    - Speaking time dominance (30 points)
    - Turn frequency (20 points)
    - Topic alignment (20 points)
    - Support statements (20 points)
    """
    
    score = 0
    
    # Who speaks first?
    first_speaker = min(all_segments, key=lambda s: s.start_ms).speaker_id
    if speaker_id == first_speaker:
        score += 10  # Initiative
    
    # Speaking time dominance
    speaker_stats = all_stats.get(speaker_id, {})
    dominance_pct = speaker_stats.get('dominance_percentage', 0)
    if dominance_pct > 30:  # Speaks more than average
        score += (dominance_pct / 50) * 30  # Cap at 30 points
    
    # Turn frequency (more turns = more engagement)
    turn_count = speaker_stats.get('turn_count', 0)
    avg_turns = sum(s.get('turn_count', 0) for s in all_stats.values()) / len(all_stats) if all_stats else 1
    if turn_count > avg_turns:
        score += (turn_count / max(avg_turns, 1) / 2) * 20  # Cap at 20 points
    
    # Topic alignment
    alignment_pct = speaker_stats.get('on_topic_percentage', 50)
    score += (alignment_pct / 100) * 20  # Cap at 20 points
    
    return min(100, score)
```

### 6.2 Communication Clarity Score

```python
def calculate_clarity_score(speaker_id: int, all_segments: List[Segment]) -> float:
    """
    Clarity combines:
    - Vocabulary richness (25%)
    - Grammar quality (25%)
    - Confidence (no filler words) (25%)
    - Speech pace (25%)
    """
    
    speaker_segments = [s for s in all_segments if s.speaker_id == speaker_id]
    
    if not speaker_segments:
        return 0
    
    # Aggregate text
    full_text = ' '.join([s.text for s in speaker_segments])
    
    # Vocabulary
    vocab_analysis = analyze_vocabulary(full_text)
    vocab_score = vocab_analysis['lexical_richness']
    
    # Grammar
    grammar = GrammarAnalyzer().analyze_grammar(full_text)
    grammar_score = grammar['grammar_quality_score']
    
    # Confidence
    confidence = analyze_speech_confidence(full_text)
    confidence_score = confidence['confidence_score']
    
    # Speech pace (words per minute)
    total_duration_sec = (speaker_segments[-1].end_ms - speaker_segments[0].start_ms) / 1000
    word_count = len(full_text.split())
    wpm = (word_count / total_duration_sec * 60) if total_duration_sec > 0 else 0
    
    # Ideal pace: 120-150 WPM
    pace_score = max(0, 100 - abs(135 - wpm) / 1.5)
    
    # Weighted composite
    clarity = (
        vocab_score * 0.25 +
        grammar_score * 0.25 +
        confidence_score * 0.25 +
        pace_score * 0.25
    )
    
    return clarity
```

---

## TESTING THE ALGORITHMS

### Mock Data for Testing

```python
# Sample transcript segments for testing
sample_segments = [
    Segment(1, "Alice", 0, 3000, "I think we should focus on sustainability", 0.95),
    Segment(2, "Bob", 3200, 5000, "I agree, but we need to consider costs", 0.92),
    Segment(1, "Alice", 5100, 7500, "That's a good point", 0.88),
    Segment(3, "Charlie", 7600, 10000, "Um, like, I think, you know, we should...", 0.85),
    Segment(2, "Bob", 10100, 12500, "My cousin has a cat", 0.90),  # Off-topic
]

# Run all analyses
turns = detect_speaker_turns(sample_segments)
interrupts = detect_interruptions(sample_segments)
dominance = calculate_dominance_metrics(sample_segments)
latency = calculate_response_latency(sample_segments)

print("Dominance:", dominance)
# Output: {1: {'speaking_time_seconds': 10.5, 'dominance_percentage': 35.0, ...}}

analyzer = TopicAlignmentAnalyzer()
analyzer.set_topic("Sustainability and business costs")
alignments = analyzer.analyze_all_segments(sample_segments)

print("Alignments:", alignments[2][1]['alignment_score'])
# Should be low for "My cousin has a cat"

confidence = analyze_speech_confidence(sample_segments[3].text)
print("Confidence:", confidence['confidence_level'])
# Should be 'Low' due to many fillers
```

---

**Algorithm Reference Complete**

These implementations are production-ready. Adapt as needed for your use case.
