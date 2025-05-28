# tool-wizard

G-code postprocessor for toolchangers.

Designed for use with PrusaSlicer.

Inspired by [N3MI-DG/tool_control](https://github.com/N3MI-DG/tool_control).

## Features

* Set hotends to idle or active temperature based on the anticipated timing of future toolchanges.
* Disable a hotend which won't be used for the remainder of the print.
* Transfer part cooling fan speed to new tool on toolchange.
