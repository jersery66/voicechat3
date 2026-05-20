# Graph Report - .  (2026-05-20)

## Corpus Check
- Large corpus: 80 files �� ~1,504,369 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary
- 148 nodes · 175 edges · 32 communities (10 shown, 22 thin omitted)
- Extraction: 70% EXTRACTED · 30% INFERRED · 0% AMBIGUOUS · INFERRED: 53 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_User Interface Layer|User Interface Layer]]
- [[_COMMUNITY_Configuration & Agent Prompts|Configuration & Agent Prompts]]
- [[_COMMUNITY_Knowledge Base & Therapy Scenes|Knowledge Base & Therapy Scenes]]
- [[_COMMUNITY_Game Engine & Therapeutic Mechanics|Game Engine & Therapeutic Mechanics]]
- [[_COMMUNITY_Conversation Pipeline (with Agent)|Conversation Pipeline (with Agent)]]
- [[_COMMUNITY_RAG Knowledge Retrieval|RAG Knowledge Retrieval]]
- [[_COMMUNITY_Clinical Psychology Framework|Clinical Psychology Framework]]
- [[_COMMUNITY_UI Design & Documentation|UI Design & Documentation]]
- [[_COMMUNITY_Dialog System|Dialog System]]
- [[_COMMUNITY_Error Monitoring|Error Monitoring]]
- [[_COMMUNITY_Psychotic Symptom Knowledge|Psychotic Symptom Knowledge]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]

## God Nodes (most connected - your core abstractions)
1. `AgentService (3B Model Agent)` - 15 edges
2. `RAGService` - 13 edges
3. `MainWindow` - 13 edges
4. `Media Download Script` - 11 edges
5. `Voice Chat Configuration` - 9 edges
6. `GameEngine` - 9 edges
7. `Logger (get_logger)` - 7 edges
8. `ConversationPipeline` - 7 edges
9. `GameEngine (Main Game Loop)` - 7 edges
10. `StormSystem` - 6 edges

## Surprising Connections (you probably didn't know these)
- `MainWindow` --references--> `my_voice.txt TTS Script`  [INFERRED]
  ui/main_window.py → data/my_voice.txt
- `DataManager (Hierarchical Data Storage)` --semantically_similar_to--> `ClinicalTracker (CSV Event Recording)`  [INFERRED] [semantically similar]
  data/data_manager.py → game/clinical_tracker.py
- `DataManager` --semantically_similar_to--> `GameEngine`  [INFERRED] [semantically similar]
  data/data_manager.py → game/engine.py
- `DataManager` --semantically_similar_to--> `ClinicalTracker`  [INFERRED] [semantically similar]
  data/data_manager.py → game/clinical_tracker.py
- `Crisis Intervention and Suicide Prevention` --conceptually_related_to--> `AgentService (3B Model Agent)`  [INFERRED]
  knowledge_base/knowledge.json → services/agent_service.py

## Hyperedges (group relationships)
- **Therapeutic Game Subsystem Orchestration** — game_engine, game_clinicaltracker, game_player, game_resourcesystem, game_stormsystem, game_campsystem, game_difficultysystem, game_backgroundsystem [EXTRACTED 1.00]
- **Cross-Session Data Persistence** — data_datamanager, data_treatmentprogress, config_config [INFERRED 0.85]
- **Crisis Detection and Intervention** — config_agent_crisis, config_crisis_intervention, game_stormsystem, config_system_prompt [INFERRED 0.75]
- **Clinical Knowledge to Therapeutic Media Scene Mapping** — knowledge_sleep_intervention, knowledge_anxiety_intervention, knowledge_depression_intervention, knowledge_anger_management, knowledge_relaxation_techniques, scene_sleep_aid, scene_anxiety_relief, scene_depression_support, scene_anger_calm, scene_breathing_exercise, scene_muscle_relaxation, scene_meditation [INFERRED 0.85]
- **Pipeline Orchestration of Core Services** — pipeline, agent_service, rag_service, llm_service, emotion_tracker [EXTRACTED 1.00]
- **RAG Knowledge Retrieval and Intent Routing Flow** — rag_service, agent_service, knowledge_core, emollm_single_turn_1, emollm_single_turn_2, psyqa_converted, synonym_map_concept, intent_routing_concept, lazy_loading_concept [EXTRACTED 1.00]
- **Frosted Glass Panel Composition Pattern** — widget_frosted_panel, ui_chat_panel, ui_control_panel, rationale_frosted_glass_theme [EXTRACTED 1.00]
- **Session End UI Flow** — ui_main_window, dialog_session_end, dialog_crisis, dialog_continue_or_end, ui_control_panel [INFERRED 0.85]
- **Researcher Tools Access Points** — ui_control_panel, ui_session_review, ui_stats_panel, rationale_user_researcher_split [EXTRACTED 1.00]
- **Game Engine System Architecture (composition of all game subsystems)** — engine_GameEngine, clinical_tracker_ClinicalTracker, player_Player, resource_system_ResourceSystem, storm_system_StormSystem, camp_system_CampSystem, difficulty_system_DifficultySystem, background_system_BackgroundSystem [EXTRACTED 1.00]
- **Therapeutic Game Mechanics (clinical interventions encoded as game design patterns)** — resource_system_ResourceSystem, storm_system_StormSystem, camp_system_CampSystem, difficulty_system_DifficultySystem, rationale_GoNogoTask, rationale_478Breathing, rationale_DelayedGratification, rationale_DDA [EXTRACTED 1.00]
- **Clinical Knowledge Pipeline (agent prompts plus knowledge base for RAG-augmented counseling)** — config_AgentIntentMessage, config_AgentEmotionMessage, config_AgentCrisisMessage, config_AgentRagRoutingMessage, config_AgentRelaxationMessage, cpsycounr_converted_ClinicalCases, emollm_multi_turn_Dialogues [INFERRED 0.75]

## Communities (32 total, 22 thin omitted)

### Community 0 - "User Interface Layer"
Cohesion: 0.14
Nodes (21): my_voice.txt TTS Script, PDF Report Background, Counselor Portrait (3-dots filename), Red Institutional Document Background, UI Main Background, UI Warm Background Variant, User-Facing vs Researcher UI Separation, ChatPanel (+13 more)

### Community 1 - "Configuration & Agent Prompts"
Cohesion: 0.19
Nodes (20): AGENT_CRISIS_SYSTEM_MESSAGE, AGENT_EMOTION_SYSTEM_MESSAGE, AGENT_INTENT_SYSTEM_MESSAGE, AGENT_RELAXATION_SYSTEM_MESSAGE, Voice Chat Configuration, CRISIS_INTERVENTION_SUFFIX, GREETING_VARIANTS, SYSTEM_PROMPT - MI Counseling Rules (+12 more)

### Community 2 - "Knowledge Base & Therapy Scenes"
Cohesion: 0.13
Nodes (19): Media Download Script, Anger Emotion Management, Anxiety Emotion Intervention, Crisis Intervention and Suicide Prevention, Depression Emotion Intervention, Relaxation Training Techniques, Sleep Intervention Techniques, Withdrawal Symptom Coping (+11 more)

### Community 3 - "Game Engine & Therapeutic Mechanics"
Cohesion: 0.13
Nodes (19): BackgroundSystem (Dynamic Background Evolution), CampSystem (Delayed Gratification Building), ClinicalTracker (CSV Event Recording), Agent Emotion Analysis Prompt, Agent Relaxation Type Classifier Prompt, Emotion-Scene Mapping, DataManager (Hierarchical Data Storage), TreatmentProgress (Cross-Session Tracking) (+11 more)

### Community 4 - "Conversation Pipeline (with Agent)"
Cohesion: 0.22
Nodes (13): EmotionTracker, GameService, LLMService, Logger (get_logger), Metrics Collector, Parallel Intent+Emotion Classification, ConversationPipeline, PDFReportGenerator (+5 more)

### Community 5 - "RAG Knowledge Retrieval"
Cohesion: 0.23
Nodes (12): Configuration Health Check, EmoLLM Single-Turn Dataset 1, EmoLLM Single-Turn Dataset 2, Intent Routing (Rule-based + 3B Agent), Core Knowledge Base (knowledge.json), Lazy Loading for Large Knowledge Bases, Knowledge Base Preprocessing Script, Psychology Terms Trie (PSYCHOLOGY_TERMS) (+4 more)

### Community 6 - "Clinical Psychology Framework"
Cohesion: 0.25
Nodes (8): Agent Crisis Risk Assessment Prompt, Crisis Intervention Suffix, System Prompt (MI Counseling Rules), Clinical Psychology Case Collection (20+ cases), Multi-Turn Counseling Dialogues (30+ dialogues), Cognitive Behavioral Therapy (CBT) Approach, Motivational Interviewing OARS Techniques, Psychosomatic Symptom Model (mind-body interaction)

### Community 7 - "UI Design & Documentation"
Cohesion: 0.40
Nodes (6): CLAUDE.md Project Guidance, README.md Project Documentation, Chinese Ink Wash Painting Color Palette, Frosted Glass UI Design Pattern, Queue-Based Thread Safety Pattern, UI Styles (GLOBAL_STYLE, GLOBAL_STYLE_DARK, COLORS)

### Community 8 - "Dialog System"
Cohesion: 0.50
Nodes (4): BaseDialog, ContinueOrEndDialog, CrisisDialog, SessionEndDialog

## Knowledge Gaps
- **46 isolated node(s):** `TreatmentProgress`, `data package init`, `game package init`, `game.entities package init`, `game.systems package init` (+41 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.