# DJV.TOOLS Studio Architecture

## Vision
**djv.tools** is an end-to-end AI production studio designed to bridge the gap between cinematic intent and generative execution. We provide a suite of autonomous agents that mirror traditional film departments, empowering creators to build films from concept to final render within a unified ecosystem.

Our goal is not just to generate images, but to facilitate a **production workflow** where every tool has a defined role in the lifecycle of the art.

---

## Core Philosophy
1.  **Studio Identity:** Naming conventions reflect industry roles (`DoP`, `Prep`) rather than generic tech terms, grounding users in professional workflows.
2.  **Technical Safety:** Namespace collisions and environment conflicts are prioritized (e.g., avoiding `.dev` suffixes) to ensure long-term stability.
3.  **Iterative Creation:** While the modules follow a logical order, the workflow is non-linear; tools can be revisited at any stage.

---

## Module Registry

The studio consists of four core agents, each corresponding to a phase of production. All internal namespaces are lowercase for technical safety (`djv.tools.xxx`), while UI labels use Title Case.

| Namespace | Display Name | Role | Functionality Scope |
| :--- | :--- | :--- | :--- |
| `djv.tools.story` | **Story** | Development | Narrative development, outlining, script formatting. Handles the text foundation of the film. |
| `djv.tools.prep` | **Prep** | Pre-Production | Storyboarding, visual planning, asset preparation. Converts text beats into visual concepts. |
| `djv.tools.dop` | **DoP** | Director of Photography | Prompt engineering, lighting design, lens simulation. Defines the visual language and style. |
| `djv.tools.gen` | **Gen** | Generation | Asset rendering, diffusion execution. The final output engine for images/video production. |

---

## Workflow Example

The following sequence demonstrates a standard project creation flow:

1.  **Development:** User initiates the project with `djv.tools.story`.
    *   *Command:* `djv story outline --genre sci-fi`
2.  **Pre-Production:** Visualize key moments using `djv.tools.prep`.
    *   *Action:* Generate storyboard panels for Scene 3.
3.  **Visual Design:** Define the look with `djv.tools.dop`.
    *   *Command:* `djv dop style --lighting moody, lens anamorphic`
4.  **Execution:** Render final assets using `djv.tools.gen`.
    *   *Action:* Push prompt to rendering engine and download output.

*Note: Users may loop back from Gen to Story or Prep at any time.*

---

## Technical Constraints & Standards

1.  **Namespace Naming:** Always use lowercase snake_case for internal imports (e.g., `djv.tools.story`).
2.  **Collision Safety:** Avoid reserved suffixes like `.dev` or `.env`. All modules must be unique within the package structure.
3.  **API Consistency:** All agents follow a standard interface: `init()`, `execute(prompt)`, `export(format)`.

---

## Future Roadmap

To maintain scalability, the following namespaces are reserved for future phases not currently included in v1.0:

| Namespace | Intended Phase |
| :--- | :--- |
| `djv.tools.post` | Post-Production (Editing, Color Grading) |
| `djv.tools.dist` | Distribution (Export Formats, Compression) |

*Warning:* Do not repurpose existing modules (`story`, `prep`, etc.) for these functions without a major version bump.

---

## Version Control
*   **Current Version:** 1.0.0 (Creation Suite)
*   **Status:** Active Development
*   **Last Updated:** [Insert Date]