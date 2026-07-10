# Codebook (auto-generated - do not edit; regenerated on every build)

## Mechanisms (priority order; the highest-priority match applies)

**ban** - strength 5 (prohibition), BLOCK. Triggers: permanent ban, permanently ban, indefinite ban, indefinitely ban, outright ban, ban on data cent, banned data cent, ban on new data ...
**moratorium** - strength 4 (halt), BLOCK. Triggers: moratorium, moratoria, pause on data, puts a hold, hits pause, pushes pause, hold on data center, temporary pause ...
**project_denial** - strength 4 (halt), BLOCK. Triggers: project_withdrawal, denied the rezoning, denied the application, rejected the application, denied the permit, denied the special use, denied the special-use, withdrew its ...
**conditional_zoning** - strength 3 (conditional), non-block. Triggers: zoning_restriction, setback, water-use agreement, water use agreement, noise limit, noise ordinance, decibel limit, buffer ...
**infrastructure_opposition** - strength 3 (conditional), non-block. Triggers: transmission line, transmission corridor, powerline, power line, substation, pipeline, 67-mile, transmission project
**cost_allocation** - strength 2 (financial/disclosure), non-block. Triggers: ratepayer, utility cost, large load tariff, large-load tariff, tariff, cost allocation, cost-allocation, cost causation ...
**incentive_repeal** - strength 2 (financial/disclosure), non-block. Triggers: tax incentive, tax abatement, tax break, tax exemption, sales tax exemption, eliminate the incentive, revoke the incentive, repeal the incentive ...
**community_benefit** - strength 2 (financial/disclosure), non-block. Triggers: community benefit agreement, community_benefit_agreement, host agreement, host community agreement, property tax to surrounding, neighborhood fund
**disclosure** - strength 2 (financial/disclosure), non-block. Triggers: disclosure, transparency, reporting requirement, report annually, public reporting, water usage reporting, energy usage reporting, annual report
**litigation** - strength 2 (financial/disclosure), non-block. Triggers: lawsuit,  sued , litigation, filed suit, filed a suit, injunction, appeal, court challenge ...
**study** - strength 1 (procedural/advocacy), non-block. Triggers: study_or_report, quantify damages, quantify monetary, feasibility study, impact study, commission a study, research report, explores limiting
**public_pressure** - strength 1 (procedural/advocacy), non-block. Triggers: public_comment, public comment, petition, rally, protest, residents objected, packed the, spoke against ...

## Concerns (grievances; independent of mechanism)

**noise**: noise, decibel, sound limit, loud, hum 
**water**: water usage, water use, water consumption, aquifer, groundwater, wells, water supply, water study ...
**light**: light pollution, lighting, glare
**energy_cost**: ratepayer, utility cost, electric bill, energy cost, rate increase, rate hike, raising utility, power bill
**grid_strain**: grid, power demand, electricity demand, load growth, transmission, substation, capacity strain
**environment**: environmental, emissions, diesel generator, air quality, wetland, habitat, pollution, carbon
**land_use**: farmland, agricultural land, rural character, green space, open space, prime farm, rezon
**property_value**: property value, home value, property tax burden
**traffic**: traffic, road damage, truck
**health**: health, asthma, cancer

## Outcome ladder

blocked_confirmed - the opposed project/measure was verifiably stopped (independent finality evidence: terminal status or bill stage)
restricted_conditional - a conditional restriction was imposed; the project was NOT stopped
blocked_unverified - recorded as stopped, without independent evidence
advanced_confirmed / advanced_unverified - the project/measure advanced; verified / unverified

These labels describe what happened to the opposed project or measure. Scorekeeping terms ('win'/'loss') appear only when quoting the raw Community Outcome field, always in quotes, and are never used in derived statistics: a denied permit, an enacted moratorium, and a defeated bill are different events - pair every grade with qc_mechanism.
mixed / pending - as recorded / everything else

Finality evidence codes: bill_stage > terminal_status > outcome_label_only (never sufficient for *_confirmed) > none.
