# Wavebird

## Plots historical price data against historical DCF calculations

DCF is generated using Free Cash Flow from Equity and Cost of Equity as discount rate.  
COE is generated from data in HistoricalRates.csv  
COE = risk-free-rate + (beta * equity-risk-premium)  

Historical Data from:  
https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm

TODO:

- [ ] API caching
- [ ] Monte Carlo 
- [ ] Efficient Frontier optimization with GA
- [ ] Port to Rust
- [ ] Host as a web application?
