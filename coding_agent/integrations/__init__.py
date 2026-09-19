"""External capability clients owned by Coding Agent.

Excel Analyzer is invoked through a dedicated subprocess client. It is never
imported into the Coding Agent process, and the built-in `execute` shell tool
is not used as the transport.
"""
