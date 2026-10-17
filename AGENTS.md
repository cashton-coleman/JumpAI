# Working preferences

- No extensive testing. Run only focused checks needed to validate the change; expand testing only when requested or a concrete failure requires it.
- No over-engineering. Prefer the smallest straightforward solution that meets the request.
- No over-reporting. Keep updates and final responses brief; report the outcome and material blockers without lengthy narratives or unsolicited reports.

# Code quality

- Apply these requirements to everything written for JumpAI, including scripts, examples, and tests.
- Write concise, readable code with clear names and straightforward control flow. Avoid clever shortcuts, dense expressions, and unnecessary abstractions.
- Separate logical sections of code with blank lines. Give each section a clear, descriptive comment header.
- Document every function and method with a comment or docstring explaining its purpose, inputs, outputs, side effects, and failure behavior where applicable. Document file/module headers with their purpose and responsibilities. Keep comments accurate and useful.
- Treat bulletproof, defense-grade reliability as the engineering goal: validate inputs and trust boundaries, handle foreseeable failures explicitly, preserve data integrity, manage resources safely, and fail safely with actionable errors. Never silently swallow failures.
- Use focused checks for meaningful failure cases and security boundaries. Keep the implementation proportional to the task; reliability does not justify unnecessary complexity or exhaustive testing.
- Do not claim code is bulletproof, DoD-certified, or compliant with a defense standard without evidence and an explicitly defined standard. State material limitations plainly and briefly.
