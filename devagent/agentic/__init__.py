"""DevAgent Unified Architecture — autonomous reasoning with full pipeline tools.

The unified architecture merges V1's professional pipeline agents into V2's
ReAct loop as callable tools. All task modes use the same execution engine.

Core components:
  tools:          22+ tools including full pipeline (requirements→design→code→test)
  pipeline_tools: V1 agent adapters (analyze_requirements, design_architecture, etc.)
  events:         Event bus for streaming observability
  state:          Enhanced agent state with event sourcing + checkpoints
  context:        Layered context management (RepoMap → RelevantFiles → Focus → History)
  core:           ReAct loop with interactive checkpoints (Think → Act → Observe)
  interaction:    Real-time user interaction — approval, dialogue, progress
  session:        Multi-client session management — lifecycle, replay
  review_gate:    Phase-level human quality review — LLM evaluation + human decision
  fault_locator:  SBFL + AST static + LLM fusion three-layer fault localization
  validation:     Instant validation + regression selector + mutation testing
  observability:  Streaming SSE + execution replay + task history + dashboard
  planning:       PlannerAgent task decomposition + PlanExecutor DAG execution
  experience:     Cross-task learning via bug→fix pattern vector store
  sandbox:        Docker/Podman/Local three-tier containerized execution
  multi_agent:    Coordinator + Worker parallel agent collaboration
  multimodal:     Image analysis for screenshots, diagrams, and UI issues
  verification:   Symbolic execution + contract checking for critical code
"""

from .tools import ToolRegistry, BaseTool, ToolResult
from .events import EventBus, EventType, DevAgentEvent
from .state import AgentLoopState
from .context import ContextManager
from .core import DevAgentCore, run_agentic
from .interaction import (
    InteractionController, ProgressStreamer,
    ApprovalRequest, ApprovalType, UserCommand, CommandType,
    _set_active_controller, _get_active_controller,
)
from .session import SessionManager, ClientSession, ClientRole, ClientTransport
from .review_gate import (
    PhaseReviewGate, ReviewSession, ReviewRequest, ReviewArtifact,
    QualityEvaluator, ReviewFormatter, ArtifactBuilder,
    ReviewStatus, ArtifactType, QualityLevel,
)
from .fault_locator import FaultLocalizationPipeline, FaultReport
from .validation import InstantValidator, QualityGateSystem
from .observability import StreamingServer, TaskHistoryManager, HumanInTheLoop
from .planning import PlannerAgent, PlanExecutor, ExecutionPlan
from .experience import ExperienceStore, ExperienceInjector
from .sandbox import SandboxManager, ContainerSpec
from .multi_agent import Coordinator, SharedState
from .multimodal import ImageReadTool, ScreenshotAnalyzer
from .verification import VerificationGate, SymbolicExecutor
from .pipeline_tools import register_pipeline_tools
from .state_graph import (
    StateGraph, CheckpointManager, SubgraphNode, Command, GraphState,
    ToolNode, ToolMessage, AIMessage, HumanMessage, BaseMessage,
    StateMigrator, GraphAgentCore, build_devagent_graph,
)
