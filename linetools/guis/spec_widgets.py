""" Module for spec widgets
"""
from __future__ import print_function, absolute_import, division, unicode_literals


import numpy as np
import pdb

from PyQt4 import QtGui
from PyQt4 import QtCore

from astropy.units import Quantity
from astropy import constants as const
from astropy import units as u
u.def_unit(['mAA', 'milliAngstrom'], 0.001 * u.AA, namespace=globals()) # mA

from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from astropy.modeling import models, fitting

from linetools.isgm.abssystem import GenericAbsSystem
from linetools import utils as ltu
from linetools.guis import utils as ltgu
from linetools.guis import simple_widgets
from linetools.guis import line_widgets as ltgl
from linetools.spectra.xspectrum1d import XSpectrum1D
from ..spectralline import AbsLine
from ..analysis import voigt as ltv
from .xabssysgui import XAbsSysGui


class ExamineSpecWidget(QtGui.QWidget):
    """ Widget to plot a spectrum and interactively
        fiddle about.  Akin to XIDL/x_specplot.pro

        12-Dec-2014 by JXP
    """
    def __init__(self, ispec, parent=None, status=None, llist=None,
                 abs_sys=None, norm=True, second_file=None, zsys=None,
                 key_events=True, vlines=None, plotzero=False, exten=None,
                 xlim=None, ylim=None, rsp_kwargs=None):
        """
        Parameters
        ----------
        ispec : XSpectrum1D, tuple of arrays or filename
        exten : int, optional
          extension for the spectrum in multi-extension FITS file
        parent : Widget parent, optional
        status : Point to status bar, optional
        llist : dict, optional
          Used to guide the line lists
        abs_sys : list, optional
          AbsSystem's
        zsys : float, optional
          intial redshift
        key_events : bool, optional
          Use key events? [True]
          Useful when coupling to other widgets
        xlim : tuple of two floats
          Initial x plotting limits
        ylim : tuple of two floats
          Initial y plotting limits
        """
        super(ExamineSpecWidget, self).__init__(parent)

        # Spectrum
        spec, spec_fil = ltgu.read_spec(ispec, exten=exten, norm=norm,
                                        rsp_kwargs=rsp_kwargs)
        self.orig_spec = spec  # For smoothing
        self.spec = self.orig_spec

        # determine the filename (if any)
        if isinstance(ispec, (str, basestring)):
            filename = ispec
        else:
            filename = None

        self.vlines = []
        if vlines is not None:
            self.vlines.extend(vlines)

        self.plotzero = plotzero

        # Other bits (modified by other widgets)
        self.model = None
        self.bad_model = None  # Discrepant pixels in model
        self.use_event = 1

        # Abs Systems
        if abs_sys is None:
            self.abs_sys = []
        else:
            self.abs_sys = abs_sys
        self.norm = norm
        self.psdict = {}  # Dict for spectra plotting
        self.adict = {}  # Dict for analysis
        self.init_spec(xlim=xlim, ylim=ylim)
        self.xval = None  # Used with velplt

        # Status Bar?
        if not status is None:
            self.statusBar = status

        # Line List?
        if llist is None:
            self.llist = {'Plot': False, 'List': 'None', 'z': 0., 'Lists': []}
        else:
            self.llist = llist

        # zsys
        if zsys is not None:
            self.llist['z'] = zsys

        # Create the mpl Figure and FigCanvas objects.
        # 5x4 inches, 100 dots-per-inch
        #
        self.dpi = 150  # 150
        self.fig = Figure((8.0, 4.0), dpi=self.dpi)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setParent(self)

        self.canvas.setFocusPolicy( QtCore.Qt.ClickFocus )
        self.canvas.setFocus()
        if key_events:
            self.canvas.mpl_connect('key_press_event', self.on_key)
        self.canvas.mpl_connect('button_press_event', self.on_click)

        # Make two plots
        self.ax = self.fig.add_subplot(1, 1, 1)
        self.show_restframe = False
        self.fig.subplots_adjust(hspace=0.1, wspace=0.1)
        if filename is not None:
            self.fig.suptitle(filename)

        vbox = QtGui.QVBoxLayout()
        vbox.addWidget(self.canvas)

        self.setLayout(vbox)

        # Draw on init
        self.on_draw()

    # Setup the spectrum plotting info
    def init_spec(self, xlim=None, ylim=None):
        """ Initialize parameters for plotting the spectrum
        """
        #xy min/max
        if xlim is None:
            xmin = np.min(self.spec.wavelength.value)
            xmax = np.max(self.spec.wavelength.value)
        else:
            xmin, xmax = xlim
        if ylim is None:
            from linetools.spectra.plotting import get_flux_plotrange
            ymin, ymax = get_flux_plotrange(self.spec.flux.value)
        else:
            ymin, ymax = ylim
        #QtCore.pyqtRemoveInputHook()
        #xdb.set_trace()
        #QtCore.pyqtRestoreInputHook()
        self.psdict['x_minmax'] = np.array([xmin, xmax])
        self.psdict['y_minmax'] = [ymin, ymax]
        self.psdict['sv_xy_minmax'] = [[xmin, xmax], [ymin, ymax]]
        self.psdict['tmp_xy'] = None
        self.psdict['nav'] = ltgu.navigate(0, 0, init=True)
        # Analysis dict
        self.adict['flg'] = 0  # Column density flag

    def on_key(self, event):
        """ Deals with key events

        Parameters
        ----------
        event : event object
        """
        # Flag to control re-draw
        flg = -1

        # NAVIGATING
        if event.key in self.psdict['nav']:
            flg = ltgu.navigate(self.psdict, event,
                                flux=self.spec.flux.value,
                                wave=self.spec.wavelength.value)

        # DOUBLETS
        if event.key in ['C', 'M', 'X', '4', '8', 'B']:
            wave, name = ltgu.set_doublet(self, event)
            # Lines
            self.ax.plot([wave[0]]*2, self.psdict['y_minmax'], '--', color='red')
            self.ax.plot([wave[1]]*2, self.psdict['y_minmax'], '--', color='red')
            # Name
            for wv in wave:
                self.ax.text(wv, self.psdict['y_minmax'][0]+0.8*(
                    self.psdict['y_minmax'][1]-self.psdict['y_minmax'][0]), name, color='red')
            flg = 2  # Layer

        ## SMOOTH
        if event.key == 'S':
            self.spec = self.spec.box_smooth(2)
            flg = 1
        if event.key == 'U':
            self.spec = self.orig_spec
            flg = 1

        ## Lya Profiles
        if event.key in ['D', 'R']:
            # Set NHI
            if event.key == 'D':
                NHI = 10**20.3 * u.cm**-2
            elif event.key == 'R':
                NHI = 10**19.0 * u.cm**-2
            zlya = event.xdata/1215.6701 - 1.
            self.llist['z'] = zlya
            # Generate Lya profile
            lya_line = AbsLine(1215.6701*u.AA)
            lya_line.attrib['z'] = zlya
            lya_line.attrib['N'] = NHI
            lya_line.attrib['b'] = 30. * u.km/u.s
            lya_spec = ltv.voigt_from_abslines(self.spec.wavelength, lya_line, fwhm=3.)
            lconti = event.ydata
            self.lya_line = XSpectrum1D.from_tuple((lya_spec.wavelength, lya_spec.flux*lconti))
            self.adict['flg'] = 4
            # QtCore.pyqtRemoveInputHook()
            # import pdb; pdb.set_trace()
            # QtCore.pyqtRestoreInputHook()

            flg = 1

        # ANALYSIS:  AODM, EW, Stats, Gaussian
        if event.key in ['N', 'E', '$', 'G']:
            # If column check for line list
            #QtCore.pyqtRemoveInputHook()
            #xdb.set_trace()
            #QtCore.pyqtRestoreInputHook()
            if (event.key in ['N', 'E']) & (self.llist['List'] == 'None'):
                print('xspec: Choose a Line list first!')
                try:
                    self.statusBar().showMessage('Choose a Line list first!')
                except AttributeError:
                    pass
                self.adict['flg'] = 0
                return
            flg = 1

            if (self.adict['flg'] == 0) or (self.adict['flg'] > 2):
                self.adict['wv_1'] = event.xdata # wavelength
                self.adict['C_1'] = event.ydata # local continuum
                self.adict['flg'] = 1 # Plot dot
            else:
                self.adict['wv_2'] = event.xdata # wavelength
                self.adict['C_2'] = event.ydata # local continuum
                self.adict['flg'] = 2 # Ready to plot + print

                # Sort em + make arrays
                iwv = np.array(sorted([self.adict['wv_1'],
                                       self.adict['wv_2']])) * self.spec.units['wave']
                ic = np.array(sorted([self.adict['C_1'],
                                      self.adict['C_2']]))

                # Calculate the continuum (linear fit)
                param = np.polyfit(iwv, ic, 1)
                cfunc = np.poly1d(param)
                lconti = cfunc(self.spec.wavelength.value)  # Local continuum

                if event.key == '$': # Simple stats
                    pix = self.spec.pix_minmax(iwv)[0]
                    mean = np.mean(self.spec.flux[pix])
                    median = np.median(self.spec.flux[pix])
                    stdv = np.std(self.spec.flux[pix]-lconti[pix])
                    S2N = median / stdv
                    mssg = 'Mean={:g}, Median={:g}, S/N={:g}'.format(
                            mean,median,S2N)
                elif event.key == 'G':  #  Fit a Gaussian
                    # Good pixels
                    pix = self.spec.pix_minmax(iwv)[0]
                    # EW
                    EW = np.sum(lconti[pix]-self.spec.flux[pix])
                    if EW > 0.:  # Absorption line
                        sign=-1
                    else:  # Emission
                        sign=1
                    # Amplitude
                    Aguess = np.max(self.spec.flux[pix]-lconti[pix])
                    Cguess = np.mean(self.spec.wavelength[pix])
                    sguess = 0.1*np.abs(self.adict['wv_1']-self.adict['wv_2'])
                    #QtCore.pyqtRemoveInputHook()
                    #pdb.set_trace()
                    #QtCore.pyqtRestoreInputHook()
                    g_init = models.Gaussian1D(amplitude=Aguess, mean=Cguess, stddev=sguess)
                    fitter = fitting.LevMarLSQFitter()
                    parm = fitter(g_init, self.spec.wavelength[pix].value,
                                  sign*(self.spec.flux[pix]-lconti[pix]))
                    g_final = models.Gaussian1D(amplitude=parm.amplitude.value,
                                                mean=parm.mean.value, stddev=parm.stddev.value)
                    # Plot
                    model_Gauss = g_final(self.spec.wavelength.value)
                    self.model = XSpectrum1D.from_tuple((self.spec.wavelength, lconti + sign*model_Gauss))
                    # Message
                    mssg = 'Gaussian Fit: '
                    mssg = mssg+' ::  Mean={:g}, Amplitude={:g}, sigma={:g}, flux={:g}'.format(
                            parm.mean.value, parm.amplitude.value, parm.stddev.value,
                            parm.stddev.value*(parm.amplitude.value-np.median(lconti[pix]))*np.sqrt(2*np.pi))
                else:
                    # Find the spectral line (or request it!)
                    rng_wrest = iwv / (self.llist['z']+1)
                    gdl = np.where( (self.llist[self.llist['List']].wrest-rng_wrest[0]) *
                                    (self.llist[self.llist['List']].wrest-rng_wrest[1]) < 0.)[0]
                    if len(gdl) == 1:
                        wrest = self.llist[self.llist['List']].wrest[gdl[0]]
                    else:
                        if len(gdl) == 0: # Search through them all
                            gdl = np.arange(len(self.llist[self.llist['List']]))
                        sel_widg = ltgl.SelectLineWidget(self.llist[self.llist['List']]._data[gdl])
                        sel_widg.exec_()
                        line = sel_widg.line
                        #wrest = float(line.split('::')[1].lstrip())
                        quant = line.split('::')[1].lstrip()
                        spltw = quant.split(' ')
                        wrest = Quantity(float(spltw[0]), unit=spltw[1])
                    # Units
                    if not hasattr(wrest,'unit'):
                        # Assume Ang
                        wrest = wrest * u.AA

                    # Generate the Spectral Line
                    aline = AbsLine(wrest,linelist=self.llist[self.llist['List']])
                    aline.attrib['z'] = self.llist['z']
                    # Generate a temporary spectrum for analysis and apply the local continuum
                    tspec = XSpectrum1D.from_tuple((self.spec.wavelength,
                                                    self.spec.flux, self.spec.sig))
                    tspec.normalize(lconti)
                    aline.analy['spec'] = tspec

                    # AODM
                    if event.key == 'N':
                        # Calculate the velocity limits and load-up
                        aline.analy['vlim'] = const.c.to('km/s') * (
                            ( iwv/(1+self.llist['z']) - wrest) / wrest )

                        # AODM
                        #QtCore.pyqtRemoveInputHook()
                        #xdb.set_trace()
                        #QtCore.pyqtRestoreInputHook()
                        aline.measure_aodm()
                        mssg = 'Using '+ aline.__repr__()
                        mssg = mssg + ' ::  logN = {:g} +/- {:g}'.format(
                            aline.attrib['logN'], aline.attrib['sig_logN'])
                    elif event.key == 'E':  #EW
                        aline.analy['wvlim'] = iwv
                        aline.measure_restew()
                        mssg = 'Using '+ aline.__repr__()
                        mssg = mssg + ' ::  Rest EW = {:g} +/- {:g}'.format(
                            aline.attrib['EW'].to(mAA), aline.attrib['sig_EW'].to(mAA))
                # Display values
                try:
                    self.statusBar().showMessage(mssg)
                except AttributeError:
                    pass
                print(mssg)


        ## Velocity plot
        if event.key == 'v':
            z=self.llist['z']
            # Launch
            #QtCore.pyqtRemoveInputHook()
            #xdb.set_trace()
            #QtCore.pyqtRestoreInputHook()
            abs_sys = GenericAbsSystem((0.,0.), z, (-300,300)*u.km/u.s)
            gui = XAbsSysGui(self.spec, abs_sys, norm=self.norm, llist=self.llist)
            gui.exec_()
            # Redraw
            flg=1

        # Dummy keys
        if event.key in ['shift', 'control', 'shift+super', 'super+shift']:
            flg = 0

        if event.key == '?': # open the XSpecGUI help page
            import webbrowser
            webbrowser.open("http://linetools.readthedocs.org/en/latest/xspecgui.html#navigating-these-key-strokes-help-you-explore-the-spectrum-be-sure-to-click-in-the-spectrum-panel-first")

        # Draw
        if flg==1: # Default is not to redraw
            self.on_draw()
        elif flg==2: # Layer (no clear)
            self.on_draw(replot=False)
        elif flg==-1: # Layer (no clear)
            try:
                self.statusBar().showMessage('Not a valid key!  {:s}'.format(event.key))
            except AttributeError:
                pass

    # Click of main mouse button
    def on_click(self,event):
        """ Handles mouse button events
        """
        try:
            print('button={:d}, x={:f}, y={:f}, xdata={:f}, ydata={:g}'.format(
                event.button, event.x, event.y, event.xdata, event.ydata))
        except ValueError:
            print('Out of bounds')
            return
        if event.button == 1: # Draw line
            self.xval = event.xdata
            self.ax.plot( [event.xdata,event.xdata], self.psdict['y_minmax'], ':', color='green')
            self.on_draw(replot=False)

            # Print values
            try:
                self.statusBar().showMessage('x,y = {:f}, {:g}'.format(event.xdata,event.ydata))
            except AttributeError:
                return

    # ######
    def on_draw(self, replot=True, no_draw=False):
        """ Redraws the spectrum
        no_draw: bool, optional
          Draw the screen on the canvas?
        """
        #

        if replot is True:
            self.ax.clear()
            self.ax.plot(self.spec.wavelength, self.spec.flux,
                'k-',drawstyle='steps-mid')
            try:
                self.ax.plot(self.spec.wavelength, self.spec.sig, 'r:')
            except ValueError:
                pass
            self.ax.set_xlabel('Wavelength')
            self.ax.set_ylabel('Flux')

            # Rest-frame axis
            if self.show_restframe:
                def tick_function(z, X):
                    V = X/(1+z)
                    return ["{:d}".format(int(round(x))) for x in V]
                self.ax2 = self.ax.twiny()
                self.ax2.set_xlim(self.ax.get_xlim())
                #QtCore.pyqtRemoveInputHook()
                #pdb.set_trace()
                #QtCore.pyqtRestoreInputHook()
                xtcks = self.ax.get_xticks()
                self.ax2.set_xticks(xtcks)
                z = self.rest_z
                self.ax2.set_xticklabels(tick_function(z, xtcks))
                self.ax2.set_xlabel("Rest Wavelength (z={:g})".format(z))

            # Continuum?
            if self.spec.co_is_set:
                self.ax.plot(self.spec.wavelength, self.spec.co, color='pink')

            # Model?
            if self.model is not None:
                self.ax.plot(self.model.wavelength, self.model.flux,
                    color='cyan')
                if self.bad_model is not None:
                    self.ax.scatter(self.model.wavelength[self.bad_model],
                        self.model.flux[self.bad_model],  marker='o',
                        color='red', s=3.)


            # Spectral lines?
            if self.llist['Plot'] is True:
                ylbl = self.psdict['y_minmax'][1]-0.2*(self.psdict['y_minmax'][1]-self.psdict['y_minmax'][0])
                z = self.llist['z']
                wvobs = np.array((1+z) * self.llist[self.llist['List']].wrest)
                gdwv = np.where( (wvobs > self.psdict['x_minmax'][0]) &
                                 (wvobs < self.psdict['x_minmax'][1]))[0]
                for kk in range(len(gdwv)):
                    jj = gdwv[kk]
                    wrest = self.llist[self.llist['List']].wrest[jj].value
                    lbl = self.llist[self.llist['List']].name[jj]
                    # Plot
                    self.ax.plot(wrest*np.array([z+1,z+1]), self.psdict['y_minmax'], 'b--')
                    # Label
                    self.ax.text(wrest*(z+1), ylbl, lbl, color='blue', rotation=90., size='small')


            # Abs Sys?
            if not self.abs_sys is None:
                ylbl = self.psdict['y_minmax'][0]+0.2*(self.psdict['y_minmax'][1]-self.psdict['y_minmax'][0])
                clrs = ['red', 'green', 'cyan', 'orange', 'gray', 'purple']*10
                ii=-1
                for abs_sys in self.abs_sys:
                    ii+=1
                    lines = abs_sys.list_of_abslines()
                    #QtCore.pyqtRemoveInputHook()
                    #xdb.set_trace()
                    #QtCore.pyqtRestoreInputHook()
                    wrest = Quantity([line.wrest for line in lines])
                    wvobs = wrest * (abs_sys.zabs+1)
                    gdwv = np.where( ((wvobs.value+5) > self.psdict['x_minmax'][0]) &  # Buffer for region
                                    ((wvobs.value-5) < self.psdict['x_minmax'][1]))[0]
                    for jj in gdwv:
                        if lines[jj].analy['do_analysis'] == 0:
                            continue
                        # Paint spectrum red
                        wvlim = wvobs[jj]*(1 + lines[jj].analy['vlim']/const.c.to('km/s'))
                        pix = np.where( (self.spec.wavelength > wvlim[0]) & (self.spec.wavelength < wvlim[1]))[0]
                        self.ax.plot(self.spec.wavelength[pix], self.spec.flux[pix], '-',drawstyle='steps-mid',
                                     color=clrs[ii])
                        # Label
                        lbl = lines[jj].analy['name']+' z={:g}'.format(abs_sys.zabs)
                        self.ax.text(wvobs[jj].value, ylbl, lbl, color=clrs[ii], rotation=90., size='x-small')
            # Analysis? EW, Column
            if self.adict['flg'] == 1:
                self.ax.plot(self.adict['wv_1'], self.adict['C_1'], 'go')
            elif self.adict['flg'] == 2:
                self.ax.plot([self.adict['wv_1'], self.adict['wv_2']],
                             [self.adict['C_1'], self.adict['C_2']], 'g--', marker='o')
                self.adict['flg'] = 0
            # Lya line?
            if self.adict['flg'] == 4:
                model = self.lya_line.flux
                if self.spec.co_is_set and not self.norm:
                    model *= self.spec.co
                self.ax.plot(self.spec.wavelength, model, color='green')

        # Reset window limits
        self.ax.set_xlim(self.psdict['x_minmax'])
        self.ax.set_ylim(self.psdict['y_minmax'])

        if self.plotzero:
            self.ax.axhline(0, lw=0.3, color='k')

        for line in self.vlines:
            self.ax.axvline(line, color='k', ls=':')

        # Draw
        if not no_draw:
            self.canvas.draw()

    # Notes on usage
    def help_notes(self):
        """ Not sure this is working..
        """
        doublets = [ 'Doublets --------',
                     'C: CIV',
                     'M: MgII',
                     'O: OVI',
                     '8: NeVIII',
                     'B: Lyb/Lya'
                     ]
        analysis = [ 'Analysis --------',
                     'N/N: Column density (AODM)',
                     'E/E: EW (boxcar)',
                     '$/$: stats on spectrum'
                     ]

# ######################
class VelPlotWidget(QtGui.QWidget):
    """ Widget for a velocity plot with interaction.
    Akin to XIDL/x_velplot

        19-Dec-2014 by JXP
    """
    def __init__(self, ispec, z, abs_lines=None, parent=None, llist=None, norm=True,
                 vmnx=[-300., 300.]*u.km/u.s):
        '''
        spec : XSpectrum1D
        z : float
        abs_lines: list, optional
          List of AbsLines
        llist : LineList, optional
          Input line list.  Defaults to 'Strong'
        norm : bool, optional
          Normalized spectrum?
        vmnx : Quantity array, optional
          Starting velocity range for the widget
        '''
        super(VelPlotWidget, self).__init__(parent)
        self.help_message = """
Click on any white region within the velocity plots
for the following keystroke commands to work:

i,o       : zoom in/out x limits
I,O       : zoom in/out x limits (larger re-scale)
y         : zoom out y limits
t,b       : set y top/bottom limit
l,r       : set left/right x limit
[,]       : pan left/right
C,c       : add/remove column
K,k       : add/remove row
=,-       : move to next/previous page
1,2       : Modify velocity region of the single line (left, right sides)
!,@       : Modify velocity region of all lines (left, right)
A,x       : Add/remove select line from analysis list
X         : Remove all lines from analysis list
^,&       : Flag line to be analyzed for low/high-ion kinematics
B         : Toggle as blend/no-blend  (orange color = blend)
N         : Toggle as do/do-not include for analysis  (red color = exclude)
V         : Indicate as a normal value
L         : Indicate as a lower limit
U         : Indicate as a upper limit
?         : Print this
        """

        # Initialize
        spec, spec_fil = ltgu.read_spec(ispec)

        self.spec = spec
        self.spec_fil = spec_fil
        self.z = z
        self.vmnx = vmnx
        self.norm = norm

        # Abs Lines
        if abs_lines is None:
            self.abs_lines = []
        else:
            self.abs_lines = abs_lines

        #QtCore.pyqtRemoveInputHook()
        #xdb.set_trace()
        #QtCore.pyqtRestoreInputHook()

        self.psdict = {} # Dict for spectra plotting
        self.psdict['x_minmax'] = self.vmnx.value # Too much pain to use units with this
        self.psdict['y_minmax'] = [-0.1, 1.1]
        self.psdict['nav'] = ltgu.navigate(0,0,init=True)

        # Line List
        if llist is None:
            self.llist = ltgu.set_llist('Strong')
        else:
            self.llist = llist
        self.llist['z'] = self.z

        # Indexing for line plotting
        self.idx_line = 0
        self.init_lines()

        # Create the mpl Figure and FigCanvas objects.
        self.dpi = 150
        self.fig = Figure((8.0, 4.0), dpi=self.dpi)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setParent(self)

        self.canvas.setFocusPolicy( QtCore.Qt.ClickFocus )
        self.canvas.setFocus()
        self.canvas.mpl_connect('key_press_event', self.on_key)
        self.canvas.mpl_connect('button_press_event', self.on_click)

        # Sub_plots (Initial)
        self.sub_xy = [3,4]
        self.fig.subplots_adjust(hspace=0.0, wspace=0.1)

        # Layout
        vbox = QtGui.QVBoxLayout()
        vbox.addWidget(self.canvas)
        self.setLayout(vbox)

        # Print help message
        print(self.help_message)

        # Draw on init
        self.on_draw()

    # Load them up for display
    def init_lines(self):
        wvmin = np.min(self.spec.wavelength)
        wvmax = np.max(self.spec.wavelength)
        #
        wrest = self.llist[self.llist['List']].wrest
        wvobs = (1+self.z) * wrest
        gdlin = np.where( (wvobs > wvmin) & (wvobs < wvmax) )[0]
        self.llist['show_line'] = gdlin

        # Update/generate lines [will not update]
        if len(self.abs_lines) == 0:
            for idx in gdlin:
                self.generate_line((self.z,wrest[idx]))

    def grab_line(self, wrest):
        """ Grab a line from the list
        Parameters
        ----------
        wrest

        Returns
        -------
        iline : AbsLine object
        """
        awrest = [iline.wrest for iline in self.abs_lines]
        try:
            idx = awrest.index(wrest)
        except ValueError:
            return None
        else:
            return self.abs_lines[idx]

    def generate_line(self, inp):
        """ Add a new line to the list, if it doesn't exist
        Parameters:
        ----------
        inp: tuple
          (z,wrest)
        """
        # Generate?
        if self.grab_line(inp[1]) is None:
            #QtCore.pyqtRemoveInputHook()
            #xdb.set_trace()
            #QtCore.pyqtRestoreInputHook()
            newline = AbsLine(inp[1],linelist=self.llist[self.llist['List']])
            print('VelPlot: Generating line {:g}'.format(inp[1]))
            newline.analy['vlim'] = self.vmnx/2.
            newline.attrib['z'] = self.z
            newline.analy['do_analysis'] = 1  # Init to ok
            # Spec file
            if self.spec_fil is not None:
                newline.analy['datafile'] = self.spec_fil
            # Append
            self.abs_lines.append(newline)

    def remove_line(self, wrest):
        """ Remove a line, if it exists
        Parameters
        ----------
        wrest : Quantity
        """
        awrest = [iline.wrest for iline in self.abs_lines]
        try:
            idx = awrest.index(wrest)
        except ValueError:
            return None
        else:
            _ = self.abs_lines.pop(idx)

    # Key stroke
    def on_key(self,event):

        # Init
        rescale = True
        fig_clear = False
        wrest = None
        flg = 0
        sv_idx = self.idx_line

        ## Change rows/columns
        if event.key == 'k':
            self.sub_xy[0] = max(0, self.sub_xy[0]-1)
        if event.key == 'K':
            self.sub_xy[0] = self.sub_xy[0]+1
        if event.key == 'c':
            self.sub_xy[1] = max(0, self.sub_xy[1]-1)
        if event.key == 'C':
            self.sub_xy[1] = max(0, self.sub_xy[1]+1)

        ## NAVIGATING
        if event.key in self.psdict['nav']:
            flg = ltgu.navigate(self.psdict,event)
        if event.key == '-':
            self.idx_line = max(0, self.idx_line-self.sub_xy[0]*self.sub_xy[1]) # Min=0
            if self.idx_line == sv_idx:
                print('Edge of list')
        if event.key == '=':
            self.idx_line = min(len(self.llist['show_line'])-self.sub_xy[0]*self.sub_xy[1],
                                self.idx_line + self.sub_xy[0]*self.sub_xy[1])
            if self.idx_line == sv_idx:
                print('Edge of list')

        ## Reset z
        if event.key == 'z':
            newz = ltu.z_from_v(self.z, event.xdata)
            self.z = newz
            # Drawing
            self.psdict['x_minmax'] = self.vmnx.value

        # Single line command
        if event.key in ['1','2','B','U','L','N','V','A', 'x', 'X',
                         '^', '&']:
            try:
                wrest = event.inaxes.get_gid()
            except AttributeError:
                return
            else:
                absline = self.grab_line(wrest)

        ## Velocity limits
        unit = u.km/u.s
        if event.key == '1':
            absline.analy['vlim'][0] = event.xdata*unit
        if event.key == '2':
            absline.analy['vlim'][1] = event.xdata*unit
        if event.key == '!':  # Set all lines to this value
            for iline in self.abs_lines:
                iline.analy['vlim'][0] = event.xdata*unit
        if event.key == '@':
            for iline in self.abs_lines:
                iline.analy['vlim'][1] = event.xdata*unit
        ## Line type
        if event.key == 'A': # Add to lines
            self.generate_line((self.z,wrest))
        if event.key == 'x': # Remove line
            if self.remove_line(wrest):
                print('VelPlot: Removed line {:g}'.format(wrest))
        if event.key == 'X': # Remove all lines
            # Double check
            gui = simple_widgets.WarningWidg('About to remove all lines. \n  Continue??')
            gui.exec_()
            if gui.ans is False:
                return
            #
            self.abs_lines = []  # Flush??
        # Kinematics
        if event.key == '^':  # Low-Ion
            try:
                fkin = absline.analy['flag_kin']
            except KeyError:
                fkin = 0
            fkin += (-1)**(fkin % 2**1 >= 2**0) * 2**0
            absline.analy['flag_kin'] = fkin
        if event.key == '&':  # High-Ion
            try:
                fkin = absline.analy['flag_kin']
            except KeyError:
                fkin = 0
            fkin += (-1)**(fkin % 2**2 >= 2**1) * 2**1
            absline.analy['flag_kin'] = fkin
        # Toggle blend
        if event.key == 'B':
            try:
                feye = absline.analy['flg_eye']
            except KeyError:
                feye = 0
            feye = (feye + 1) % 2
            absline.analy['flg_eye']  = feye
        # Toggle NG
        if event.key == 'N':
            try:
                fanly = absline.analy['do_analysis']
            except KeyError:
                fanly = 1
            if fanly == 0:
                fanly = 1
            else:
                fanly = 0
            absline.analy['do_analysis']  = fanly
        if event.key == 'V':  # Normal
            absline.analy['flg_limit'] = 1
        if event.key == 'L':  # Lower limit
            absline.analy['flg_limit'] = 2
        if event.key == 'U':  # Upper limit
            absline.analy['flg_limit'] = 3

        '''
        # AODM plot
        if event.key == ':':  #
            # Grab good lines
            from xastropy.xguis import spec_guis as xsgui
            gdl = [iline.wrest for iline in self.abs_sys.lines
                if iline.analy['do_analysis'] > 0]
            # Launch AODM
            if len(gdl) > 0:
                gui = xsgui.XAODMGui(self.spec, self.z, gdl, vmnx=self.vmnx, norm=self.norm)
                gui.exec_()
            else:
                print('VelPlot.AODM: No good lines to plot')
        '''

        if wrest is not None:  # Single window
            flg = 3
        if event.key in ['c','C','k','K','W','!', '@', '=', '-', 'X', 'z','R']: # Redraw all
            flg = 1
        if event.key in ['Y']:
            rescale = False
        if event.key in ['k','c','C','K', 'R']:
            fig_clear = True

        # Print help message
        if event.key == '?':
            print(self.help_message)


        if flg == 1: # Default is not to redraw
            self.on_draw(rescale=rescale, fig_clear=fig_clear)
        elif flg == 2:  # Layer (no clear)
            self.on_draw(replot=False, rescale=rescale)
        elif flg == 3:  # Layer (no clear)
            self.on_draw(in_wrest=wrest, rescale=rescale)

    # Click of main mouse button
    def on_click(self,event):
        try:
            print('button={:d}, x={:f}, y={:f}, xdata={:f}, ydata={:f}'.format(
                event.button, event.x, event.y, event.xdata, event.ydata))
        except ValueError:
            return
        if event.button == 1: # Draw line
            self.ax.plot( [event.xdata,event.xdata], self.psdict['y_minmax'], ':', color='green')
            self.on_draw(replot=False)

            # Print values
            try:
                self.statusBar().showMessage('x,y = {:f}, {:f}'.format(event.xdata,event.ydata))
            except AttributeError:
                return

    def on_draw(self, replot=True, in_wrest=None, rescale=True, fig_clear=False):
        """ Redraws the figure
        """
        #
        if replot is True:
            if fig_clear:
                self.fig.clf()
            # Loop on windows
            all_idx = self.llist['show_line']
            nplt = self.sub_xy[0]*self.sub_xy[1]
            if len(all_idx) <= nplt:
                self.idx_line = 0
            subp = np.arange(nplt) + 1
            subp_idx = np.hstack(subp.reshape(self.sub_xy[0],self.sub_xy[1]).T)
            for jj in range(min(nplt, len(all_idx))):
                try:
                    idx = all_idx[jj+self.idx_line]
                except IndexError:
                    continue # Likely too few lines
                # Grab line
                wrest = self.llist[self.llist['List']].wrest[idx]
                # Single window?
                if in_wrest is not None:
                    if np.abs(wrest-in_wrest) > (1e-3*u.AA):
                        continue

                # AbsLine for this window
                absline = self.grab_line(wrest)

                # Generate plot
                self.ax = self.fig.add_subplot(self.sub_xy[0],self.sub_xy[1], subp_idx[jj])
                self.ax.clear()

                # Zero line
                self.ax.plot( [0., 0.], [-1e9, 1e9], ':', color='gray')
                # Velocity
                wvobs = (1+self.z) * wrest
                velo = (self.spec.wavelength/wvobs - 1.)*const.c.to('km/s')

                # Plot
                self.ax.plot(velo, self.spec.flux, 'k-',drawstyle='steps-mid')

                # GID for referencing
                self.ax.set_gid(wrest)

                # Labels
                if (((jj+1) % self.sub_xy[0]) == 0) or ((jj+1) == len(all_idx)):
                    self.ax.set_xlabel('Relative Velocity (km/s)')
                else:
                    self.ax.get_xaxis().set_ticks([])
                lbl = self.llist[self.llist['List']].name[idx]
                # Kinematics
                kinl = ''
                if absline is not None:
                    if (absline.analy['flag_kin'] % 2) >= 1:
                        kinl = kinl + 'L'
                    if (absline.analy['flag_kin'] % 4) >= 2:
                        kinl = kinl + 'H'
                if absline is not None:
                    lclr = 'blue'
                else:
                    lclr = 'gray'
                self.ax.text(0.1, 0.05, lbl+kinl, color=lclr, transform=self.ax.transAxes,
                             size='x-small', ha='left')

                # Reset window limits
                #QtCore.pyqtRemoveInputHook()
                #xdb.set_trace()
                #QtCore.pyqtRestoreInputHook()
                self.ax.set_xlim(self.psdict['x_minmax'])

                # Rescale?
                if (rescale is True) & (self.norm is False):
                    gdp = np.where( (velo.value > self.psdict['x_minmax'][0]) &
                                    (velo.value < self.psdict['x_minmax'][1]))[0]
                    if len(gdp) > 5:
                        per = np.percentile(self.spec.flux[gdp],
                                            [50-68/2.0, 50+68/2.0])
                        self.ax.set_ylim((0., 1.1*per[1]))
                    else:
                        self.ax.set_ylim(self.psdict['y_minmax'])
                else:
                    self.ax.set_ylim(self.psdict['y_minmax'])

                # Fonts
                for item in ([self.ax.title, self.ax.xaxis.label, self.ax.yaxis.label] +
                         self.ax.get_xticklabels() + self.ax.get_yticklabels()):
                    item.set_fontsize(6)


                clr='black'
                if absline is not None:
                    try:
                        vlim = absline.analy['vlim']
                    except KeyError:
                        pass
                    # Color coding
                    try:  # .clm style
                        flag = absline.analy['FLAGS'][0]
                    except KeyError:
                        flag = None
                    else:
                        if flag <= 1: # Standard detection
                            clr = 'green'
                        elif flag in [2,3]:
                            clr = 'blue'
                        elif flag in [4,5]:
                            clr = 'purple'
                    # ABS ID
                    try: # NG?
                        flagA = absline.analy['do_analysis']
                    except KeyError:
                        flagA = None
                    else:
                        if (flagA>0) & (clr == 'black'):
                            clr = 'green'
                    try: # Limit?
                        flagL = absline.analy['flg_limit']
                    except KeyError:
                        flagL = None
                    else:
                        if flagL == 2:
                            clr = 'blue'
                        if flagL == 3:
                            clr = 'purple'
                    try: # Blends?
                        flagE = absline.analy['flg_eye']
                    except KeyError:
                        flagE = None
                    else:
                        if flagE == 1:
                            clr = 'orange'
                    if flagA == 0:
                        clr = 'red'

                    pix = np.where( (velo > vlim[0]) & (velo < vlim[1]))[0]
                    self.ax.plot(velo[pix], self.spec.flux[pix], '-',
                                 drawstyle='steps-mid', color=clr)
        # Draw
        self.canvas.draw()
