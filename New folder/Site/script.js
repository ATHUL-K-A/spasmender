document.addEventListener('DOMContentLoaded', () => {

    // --- Standard Values Data ---
    // You can easily add more muscles or change the values here.
    // The value represents a standard peak force measurement.
    const muscleData = [
        { 
            name: "Biceps",
            test: "Bicep Curl Peak Force",
            standardValue: 35, 
            unit: "kg" 
        },
        { 
            name: "Quadriceps",
            test: "Leg Extension Peak Force",
            standardValue: 60, 
            unit: "kg" 
        },
        { 
            name: "Grip Strength (Hand)",
            test: "Hand Dynamometer",
            standardValue: 45, 
            unit: "kg" 
        },
        { 
            name: "Deltoids (Shoulder)",
            test: "Lateral Raise Peak Force",
            standardValue: 15, 
            unit: "kg" 
        },
        {
            name: "Pectoralis Major (Chest)",
            test: "Bench Press Peak Force",
            standardValue: 80,
            unit: "kg"
        }
    ];

    // --- Get HTML Elements ---
    const muscleSelect = document.getElementById('muscle-select');
    const testTypeDisplay = document.getElementById('test-type-display');
    const standardValueDisplay = document.getElementById('standard-value-display');
    const measuredValueInput = document.getElementById('measured-value');
    const checkButton = document.getElementById('check-button');
    const resultMessage = document.getElementById('result-message');

    // --- Functions ---

    // Function to populate the dropdown menu from the data
    function populateMuscleSelector() {
        muscleData.forEach(muscle => {
            const option = document.createElement('option');
            option.value = muscle.name;
            option.textContent = muscle.name;
            muscleSelect.appendChild(option);
        });
    }

    // Function to update the displayed standard value when a new muscle is selected
    function updateDisplay() {
        const selectedMuscleName = muscleSelect.value;
        const selectedMuscle = muscleData.find(muscle => muscle.name === selectedMuscleName);

        if (selectedMuscle) {
            testTypeDisplay.textContent = selectedMuscle.test;
            standardValueDisplay.textContent = `${selectedMuscle.standardValue} ${selectedMuscle.unit}`;
        }
    }

    // Function to check the measured value against the standard
    function checkValue() {
        const selectedMuscleName = muscleSelect.value;
        const selectedMuscle = muscleData.find(muscle => muscle.name === selectedMuscleName);
        const measuredValue = parseFloat(measuredValueInput.value);

        // Clear previous results
        resultMessage.className = '';
        resultMessage.textContent = '';

        // Validate input
        if (isNaN(measuredValue)) {
            resultMessage.textContent = "Please enter a valid number.";
            resultMessage.classList.add('warning', 'visible');
            return;
        }

        const standardValue = selectedMuscle.standardValue;
        const tolerance = 0.15; // 15% tolerance
        const lowerBound = standardValue * (1 - tolerance);
        const upperBound = standardValue * (1 + tolerance);
        
        let message = ``;

        // Compare the values
        if (measuredValue >= lowerBound && measuredValue <= upperBound) {
            message = `<strong>Result:</strong> Your value of ${measuredValue} ${selectedMuscle.unit} is within the standard range for the ${selectedMuscle.name}.`;
            resultMessage.classList.add('success');
        } else {
            message = `<strong>Alert:</strong> Your value of ${measuredValue} ${selectedMuscle.unit} varies from the standard range (${lowerBound.toFixed(1)} - ${upperBound.toFixed(1)} ${selectedMuscle.unit}) for the ${selectedMuscle.name}. It may be advisable to consult with a specialist.`;
            resultMessage.classList.add('warning');
        }

        resultMessage.innerHTML = message;
        resultMessage.classList.add('visible');
    }


    // --- Event Listeners ---
    muscleSelect.addEventListener('change', updateDisplay);
    checkButton.addEventListener('click', checkValue);

    // --- Initial Setup ---
    populateMuscleSelector();
    updateDisplay(); // Set initial values on page load

});