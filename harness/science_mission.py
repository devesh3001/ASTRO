"""A coupled acquisition, finite-storage and downlink mission."""
import math
import random
from .tasks import TaskInstance, FLIGHT_MANUAL_MD, _EPS_CFG_NOMINAL, _ADCS_CFG_NOMINAL, _THERMAL_CFG_NOMINAL, _COMMS_CFG_NOMINAL


def science_store_forward_task(seed):
    rng = random.Random(seed ^ 0x5C13CE)
    starts = [rng.choice([900, 1020, 1140]), rng.choice([2700, 2820, 2940])]
    durations = [rng.choice([240, 300, 360]), rng.choice([240, 300, 360])]
    contacts = [starts[0]+durations[0]+300, rng.choice([4200, 4320, 4440])]
    contact_durations = [rng.choice([180, 240]), rng.choice([180, 240])]
    rate = rng.choice([1.6, 1.7, 1.8, 1.9, 2.0])
    loss, gain = round(rng.uniform(142, 145.5), 2), round(rng.uniform(7, 9), 2)
    tx = math.ceil((3+loss-gain-113.5+10*math.log10(4)+.2)*10)/10
    radio_load = 8+2*10**(tx/10)/1000
    off = _EPS_CFG_NOMINAL+'payload_duty_cycle = 0.0\n'
    on = _EPS_CFG_NOMINAL+'payload_duty_cycle = 1.0\n'
    comms = (_COMMS_CFG_NOMINAL.replace('tx_power_dbm = 30.0',f'tx_power_dbm = {tx}')
             .replace('baudrate_bps = 9600','baudrate_bps = 38400')
             .replace('path_loss_db = 141.5',f'path_loss_db = {loss}')
             .replace('antenna_gain_dbi = 8.0',f'antenna_gain_dbi = {gain}'))
    task = TaskInstance(
        task_id='science_store_forward', title='Science Acquisition, Finite Buffer and Downlink', difficulty='hard',
        instructions=('SCIENCE RELAY [SCI-BUFFER-417]: The instrument is idle and the radio is at its nominal rate. '
            'Acquire useful exposure in EACH protected science window, retain the generated data in finite storage, '
            'and deliver the required science payload during the two ground contacts. Read docs/mission.json for '
            'window times, per-window quotas, generation rate, buffer capacity and energy allocations. '
            'Data exists only after acquisition; a contact cannot transmit nonexistent data. Buffer overflow drops '
            'science irreversibly. Choose a feasible baud rate and transmit power, switch payload_duty_cycle over '
            'time, and use the first contact to clear space before the second science window. Every ordinary tool '
            'call advances 30 simulated seconds. Telemetry reports buffer occupancy, generated, dropped and '
            'delivered science, and per-window exposure. Final files cannot repair lost science or missed windows.'),
        files={'docs/flight_manual.md':FLIGHT_MANUAL_MD,'subsystems/eps.cfg':off,
               'subsystems/adcs.cfg':_ADCS_CFG_NOMINAL,'subsystems/thermal.cfg':_THERMAL_CFG_NOMINAL,
               'subsystems/comms.cfg':_COMMS_CFG_NOMINAL},
        reference={'subsystems/eps.cfg':off,'subsystems/comms.cfg':comms},
        initial_soc_pct=65,initial_temp_c=12,initial_pointing_error_deg=5,
        mission={'path_loss_db':loss,'antenna_gain_dbi':gain,'payload_power_w':6,
                 'science_windows':[{'start_s':s,'duration_s':d,'required_exposure_s':.9*d} for s,d in zip(starts,durations)],
                 'ground_passes':[{'start_s':s,'duration_s':d} for s,d in zip(contacts,contact_durations)],
                 'science_rate_kb_s':rate,'science_buffer_capacity_kb':round(1.05*rate*max(durations),6),
                 'max_science_loss_kb':0,'required_science_downlink_kb':round(.9*rate*sum(durations),6),
                 'required_downlink_kb':round(.9*rate*sum(durations),6),
                 'max_payload_energy_wh':round(1.2*6*sum(durations)/3600,6),
                 'max_tx_energy_wh':round(1.04*radio_load*sum(contact_durations)/3600,6),
                 'allowed_baudrates_bps':[1200,2400,4800,9600,19200,38400]},
    )
    task.reference_plan=[
        ('write_file',{'path':'subsystems/eps.cfg','content':off}),
        ('write_file',{'path':'subsystems/comms.cfg','content':comms}),
        ('step_orbit',{'minutes':starts[0]//60-1}),
        ('write_file',{'path':'subsystems/eps.cfg','content':on}),
        ('step_orbit',{'minutes':durations[0]//60}),
        ('write_file',{'path':'subsystems/eps.cfg','content':off}),
        ('step_orbit',{'minutes':(starts[1]-starts[0]-durations[0]-60)//60}),
        ('write_file',{'path':'subsystems/eps.cfg','content':on}),
        ('step_orbit',{'minutes':durations[1]//60}),
        ('write_file',{'path':'subsystems/eps.cfg','content':off}),
    ]
    return task
