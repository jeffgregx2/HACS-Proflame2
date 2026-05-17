

# Personal Project Notes

This project was created to solve an issue we had with our Mendota fireplace - how to automate so that we can turn it on with a default setting of LOW and then track when no one is in the room and turn the fireplace off.

However, it gave me an opportunity to experiment with AI coding assistants to see for myself what the cutting edge capabilities are.  Personally, I am a retired software person with over 40 years of experience in startups and larger high technology companies.   I was usually the Chief Architect, CTO, and substantial contributor to the code line.  I am certainly capable of writing the code needed for this project, but what will happen if I act at a higher level?

## My role

I decided for this project that I would not write a single line of code.  My role will be to:
* define all product requirements
* define high level testing strategy
* monitor and nudge as appropriate the AI agent to ensure we don't get stuck
* help with debugging when hardware is involved
* fully test everything to validate it functions as requested

What I didn't want to do was micro manage the AI agent.  I gave the agent as much free rein as made sense, having it propose direction, validating it against the requirements but effectively seeing what happens.

## Environment

My development environment was an Ubuntu VM on a Mac Mini (Apple Silicon).  I used the OpenAI Codex agent installed locally (CLI - v0.129.0) to enable it full access to the entire environment.  This allowed for direct file access as well as the ability to fully build and test all aspects of the project.  To make my life easier I used the CodexUI (v0.1.87) project to provide a UI for the Codex CLI agent.  For the simpler portions of the product I used a 5.4-Medium  model but moved to 5.5-High model as things got more complicated (5.5 was just released when I started this project).

The Home Assistant instance was installed in a docker container.  I leverage my normal ESPHome Builder instance to manage and deploy the ESPHome firmware.  I didn't give Codex access to this because I didn't want to grant Codex an environment that extended past the development VM.

## High Level Results

I learned a lot.  While I have used the various AI agents (ChatGPT, etc.) quite a bit in the past, I have not created this kind of environment and let the agent essentially run it.

### The Good
* The ability to create fully functioning code extremely quickly is high.  I consider myself a pretty competent software developer but I can't compete on speed.  Not even close.
* The agent was able to fully manage the environment: installing everything it needed, setting up virtual environments and essentially handling all of the DevOps that normally take a bunch of time.  I'm not claiming this was a large project, but it still takes time.
* The agent's ability to analyze data and create a plan for how to tackle a problem is pretty advanced.  Much of this project was an attempt to reverse engineer how RF signals are created.  RF is NOT in my background and thus it would have taken me quite a bit of time to understand how to do this.  Generally speaking Codex was able to analyze the signals and figure out how to use the target hardware to reliably receive the RF and convert it to validated data.
* We created quite sophisticated analysis frameworks - running all 3 hardware devices we triggered on a packet and collected the RF info on all 3 coordinated to ensure we had the same waveforms just from 3 different sources.  We used this to help understand how the waveforms were properly decoded.  There is a bit of bad here, it took multiple iterations because Codex failed to "think" through some of the obvious issue that can develop and thus we needed to iterate through problems one by one.  Still it only took a few hours to build the entire framework and start collecting real data.
* The ability to look at a problem, "understand" it, and create a solution for it is extremely high.  For example, the firmware caused a kernel fault that rebooted the ESP32 controller.  I pasted the kernel stack trace and within 1 minute a redesign of how memory was allocated (don't use the stack) was build and then deployed.  Should it have made this mistake to start?  I'll bet only really experienced developers would have done it right on the first pass.

### The Bad
* Complicated analysis can result in spinning.  As we attempted to figure out how to reliably receive and decode the RF signal, our testing ended up going in circles where after 4-5 permutations we ended back at the beginning essentially running the same tests.  I needed to intervene and force it to take a step back.  I asked a few questions to get it to consider a different route and let it go.
* Unlike a human, the Codex agent really is not able to take a broader perspective and explicitly consider ways to rule out permutations that are unlikely to work.  The Codex agent was specifically told it had full access to the rtl_433 source code, rfcat source code and had been leveraging the hardware and software as part of the analysis.  However, in the end, Codex never made the leap that deep inspection of rfcat would substantially inform it how to proceed with the CC1101 platform we were using.  After almost 1 week of letting Codex try to figure it out I had to explicitly tell it to do the rfcat analysis and compare it to how we are approaching the problem.  That analysis resulted in a working RX path within 2 hours.
* If you naively have Codex write code with no guidelines, you will end up with a low quality code line that is monolithic.  I ended up creating a deep code quality guidelines document and went back and had Codex refactor the entire code line using the guidelines.  Even then I feel quite a bit more could be done to make the code more maintainable, etc.  This area is probably the #1 item I would address at the start of any project.  Create robust guidelines and ensure they are ALWAYS obeyed while the project is being written.
* You need to micro-manage UI development.  Don't depend upon Codex to create end-user friendly UI.  Always create a requirement that the end user experience be considered when building UI otherwise you end up with technically correct UI that is extremely clunky to use.

## Summary

I'll leave it to others to decide if the resulting code is production quality and how effective it was to let Codex do essentially all of the implementation work.  I know for certain that I could not have done this project as quickly as I have.   From start to end, I spent roughly 2 weeks of wall time building this creating 2 controllers (YardStick and LilyGO) connecting to the HA integration.

For me this was valuable and a great learning opportunity.  A bit scary too.