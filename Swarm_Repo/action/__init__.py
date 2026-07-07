# action/__init__.py
# Physical engine package
# Modules:
#   gps         : WGS-84 geodetic math, Haversine, NED frame, GPS noise
#   wind        : Dryden turbulence + convective fire plume model
#   movement    : 3-D kinematic engine, Verlet integration, collision avoidance
#   sensors     : Stefan-Boltzmann thermal, Gaussian smoke plume, Heskestad flame
#   resources   : Wh battery model, litre payload, RSSI connectivity
#   scouting    : Scout orbit/grid patterns, sensor coupling
#   suppression : Approach/drop/egress, wind-corrected, drop ballistics
